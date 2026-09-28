"""HTTP health, authenticated history and sanitized exception responses."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from pbl_jobs_finder.exceptions import LLMServiceError, StateStoreError
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import create_resume_record
from pbl_jobs_finder.modules.history import HistoryService
from pbl_jobs_finder.server import create_app


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite:///{Path(self.directory.name).as_posix()}/test.db")
        self.db.initialize()
        self.history = HistoryService(self.db, token_verifier=lambda token: (
            {"phone": "13800138000"} if token == "owner" else
            {"phone": "13900139000"} if token == "other" else None))
        self.state = Mock()
        self.state.ping.return_value = True
        self.app = create_app(mount_frontend=False, database_instance=self.db,
                              state=self.state, history=self.history,
                              exports_dir=Path(self.directory.name))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db.dispose()
        self.directory.cleanup()

    async def test_redis_failure_affects_readiness_but_not_liveness(self):
        self.assertEqual((await self.client.get("/health/ready")).status_code, 200)
        self.state.ping.side_effect = StateStoreError()
        response = await self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["state"], "unavailable")
        self.assertEqual((await self.client.get("/health/live")).status_code, 200)

    async def test_history_enforces_token_and_record_ownership(self):
        with self.db.session() as session:
            record = create_resume_record(
                session, phone="13800138000", original_text="private resume",
                target_position="developer", score=80, missing_keywords=[],
                suggestions="improve", optimized_text="private optimized resume")
            record_id = record.id
        path = f"/api/history/resumes/{record_id}"
        self.assertEqual((await self.client.get(path)).status_code, 401)
        denied = await self.client.get(path, headers={"Authorization": "Bearer other"})
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("private", denied.text)
        allowed = await self.client.get(path, headers={"Authorization": "Bearer owner"})
        self.assertEqual(allowed.json()["optimized_text"], "private optimized resume")

    async def test_ai_and_unexpected_errors_do_not_stop_health(self):
        for error, status in [(LLMServiceError("AI timeout"), 503),
                              (RuntimeError("secret-internal-path"), 500)]:
            self.history.get_recent = Mock(side_effect=error)
            response = await self.client.get("/api/history", headers={
                "Authorization": "Bearer owner"})
            self.assertEqual(response.status_code, status)
            self.assertTrue(response.json()["error_id"])
            self.assertNotIn("secret-internal-path", response.text)
            self.assertEqual((await self.client.get("/health/live")).status_code, 200)

    async def test_private_download_requires_owner_and_obeys_expiry(self):
        with self.db.session() as session:
            record = create_resume_record(
                session, phone="13800138000", original_text="private resume",
                target_position="developer", score=80, missing_keywords=[],
                suggestions="improve", optimized_text="private optimized resume")
            record_id = record.id
        path = Path(self.directory.name) / f"{record_id}-{'a' * 32}.pdf"
        path.write_bytes(b"%PDF-private")
        url = f"/api/downloads/{path.name}"
        self.assertEqual((await self.client.get(url)).status_code, 401)
        self.assertEqual((await self.client.get(url, headers={"Authorization": "Bearer other"})).status_code, 403)
        response = await self.client.get(url, headers={"Authorization": "Bearer owner"})
        self.assertEqual(response.content, b"%PDF-private")
        self.assertEqual(response.headers["cache-control"], "no-store")
        session = await self.client.post("/api/session", headers={"Authorization": "Bearer owner"})
        self.assertEqual(session.status_code, 204)
        self.assertIn("HttpOnly", session.headers["set-cookie"])
        self.assertEqual((await self.client.get(url)).status_code, 200)
        await self.client.delete("/api/session")
        self.assertEqual((await self.client.get(url)).status_code, 401)
        for prefix in ("file=", "file/", "stream/"):
            self.assertEqual((await self.client.get(f"/{prefix}{path.as_posix()}")).status_code, 403)
        os.utime(path, (time.time() - 90000,) * 2)
        self.assertEqual((await self.client.get(url, headers={"Authorization": "Bearer owner"})).status_code, 404)
