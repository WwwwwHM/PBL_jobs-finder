"""FastAPI deployment entry point with the existing Gradio application mounted."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.exceptions import (
    AuthenticationError,
    PBLJobsFinderError,
    QuotaExceededError,
    StateStoreError,
)
from pbl_jobs_finder.models.database import Database, database
from pbl_jobs_finder.modules.file_retention import (
    EXPORT_TTL_SECONDS,
    clean_expired_files,
    export_path,
)
from pbl_jobs_finder.modules.history import HistoryService
from pbl_jobs_finder.modules.quota import quota_service
from pbl_jobs_finder.modules.redis_state import RedisState, configured_redis_state
from pbl_jobs_finder.utils.logging import configure_logging, report_exception

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def _token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if credentials is None:
        raise AuthenticationError("登录已失效，请重新登录")
    return credentials.credentials


def create_app(
    *, mount_frontend: bool = True,
    database_instance: Database | None = None,
    state: RedisState | None = None,
    history: HistoryService | None = None,
    exports_dir: Path | None = None,
) -> FastAPI:
    settings = get_settings()
    export_root = exports_dir or settings.exports_dir
    db = database_instance or database
    shared_state = state if state is not None else configured_redis_state()
    history_service = history or HistoryService(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        db.initialize()
        async def cleanup_loop():
            while True:
                for root in (export_root, settings.uploads_dir):
                    try:
                        await asyncio.to_thread(clean_expired_files, root)
                    except OSError as exc:
                        report_exception(logger, "storage.cleanup", exc)
                await asyncio.sleep(3600)
        cleanup = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup

    app = FastAPI(title="PBL Jobs Finder", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def private_file_boundary(request: Request, call_next):
        # Gradio's file routes authenticate neither our token nor record owner.
        # All application downloads go through the route below instead.
        path = request.url.path.lstrip("/")
        if path.startswith(("file=", "file/", "stream/")):
            return JSONResponse(status_code=403, content={"detail": "请使用已登录的下载入口"})
        response = await call_next(request)
        if path.startswith("api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/session", tags=["account"])
    def browser_session(request: Request, token: str = Depends(_token)):
        history_service.get_recent(token)
        response = Response(status_code=204)
        response.set_cookie("pbl_download_session", token, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict",
                            max_age=604800, path="/api/downloads")
        return response

    @app.delete("/api/session", tags=["account"])
    def clear_browser_session():
        response = Response(status_code=204)
        response.delete_cookie("pbl_download_session", path="/api/downloads")
        return response

    @app.get("/api/downloads/{key}", tags=["files"])
    def download(key: str, request: Request,
                 credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        token = credentials.credentials if credentials else request.cookies.get("pbl_download_session", "")
        resolved = export_path(export_root, key)
        if resolved is None:
            raise HTTPException(404, "下载文件不存在或已过期")
        record_id, path = resolved
        history_service.get_resume_detail(token, record_id)
        if not path.is_file() or time.time() - path.stat().st_mtime >= EXPORT_TTL_SECONDS:
            raise HTTPException(404, "下载文件不存在或已过期，请重新生成")
        return FileResponse(path, filename=f"resume{path.suffix}", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.exception_handler(PBLJobsFinderError)
    async def application_error(request: Request, exc: PBLJobsFinderError):
        if isinstance(exc, AuthenticationError):
            status = 401
        elif isinstance(exc, QuotaExceededError):
            status = 429
        elif isinstance(exc, PermissionError):
            status = 403
        elif isinstance(exc, RuntimeError):
            status = 503
        else:
            status = 400
        error_id = None
        if status >= 500:
            error_id = report_exception(logger, "api.request", exc)
        return JSONResponse(status_code=status, content={
            "success": False, "error": exc.to_dict(), "error_id": error_id,
        })

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        error_id = report_exception(logger, "api.request", exc)
        return JSONResponse(status_code=500, content={
            "success": False,
            "error": {"code": "10000", "message": "服务暂时不可用，请稍后重试"},
            "error_id": error_id,
        })

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        checks = {}
        try:
            with db.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except SQLAlchemyError as exc:
            report_exception(logger, "health.database", exc)
            checks["database"] = "unavailable"
        try:
            if shared_state is not None and not shared_state.ping():
                raise StateStoreError()
            checks["state"] = "redis" if shared_state is not None else "memory"
        except StateStoreError as exc:
            report_exception(logger, "health.state", exc)
            checks["state"] = "unavailable"
        healthy = "unavailable" not in checks.values()
        return JSONResponse(status_code=200 if healthy else 503, content={
            "status": "ready" if healthy else "unavailable", "checks": checks,
        })

    @app.get("/api/quota", tags=["account"])
    def quota(token: str = Depends(_token)):
        status = quota_service.status(token)
        return {**asdict(status), "remaining": status.remaining}

    @app.get("/api/history", tags=["history"])
    def recent_history(token: str = Depends(_token)):
        return asdict(history_service.get_recent(token))

    @app.get("/api/history/resumes/{record_id}", tags=["history"])
    def resume_history(record_id: int, token: str = Depends(_token)):
        return asdict(history_service.get_resume_detail(token, record_id))

    @app.get("/api/history/interviews/{session_id}", tags=["history"])
    def interview_history(session_id: int, token: str = Depends(_token)):
        return asdict(history_service.get_interview_detail(token, session_id))

    if mount_frontend:
        import gradio as gr

        from frontend import build_app

        app = gr.mount_gradio_app(
            app, build_app(), path="/",
            blocked_paths=[str(settings.uploads_dir), str(settings.chroma_dir),
                           str(settings.log_dir), str(settings.project_root / ".env"),
                           str(settings.data_dir / "job_assistant.db")],
        )
    return app
