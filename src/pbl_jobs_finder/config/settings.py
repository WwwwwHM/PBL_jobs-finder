"""Environment-backed application settings and runtime paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings used by backend modules."""

    project_root: Path
    data_dir: Path
    uploads_dir: Path
    exports_dir: Path
    chroma_dir: Path
    log_dir: Path
    log_level: str
    log_backup_count: int
    database_url: str
    app_env: str
    timezone: str
    daily_quota: int
    zhipu_api_key: str | None
    zhipu_model: str
    zhipu_timeout_seconds: float
    zhipu_max_retries: int
    aliyun_api_key: str | None
    aliyun_embedding_model: str

    def ensure_runtime_directories(self) -> None:
        """Create directories used for local persistent data."""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings from the process environment."""

    data_dir = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")).resolve()
    uploads_dir = Path(os.getenv("UPLOADS_DIR", data_dir / "uploads")).resolve()
    configured_exports_dir = os.getenv("EXPORTS_DIR", "").strip()
    exports_dir = Path(configured_exports_dir or (data_dir / "exports")).resolve()
    chroma_dir = Path(os.getenv("CHROMA_DIR", data_dir / "chroma_db")).resolve()
    configured_log_dir = os.getenv("LOG_DIR", "").strip()
    log_dir = Path(configured_log_dir or (data_dir / "logs")).resolve()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL"
        )
    default_database_url = f"sqlite:///{(data_dir / 'job_assistant.db').as_posix()}"
    database_url = os.getenv("DATABASE_URL", "").strip() or default_database_url

    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        exports_dir=exports_dir,
        chroma_dir=chroma_dir,
        log_dir=log_dir,
        log_level=log_level,
        log_backup_count=_positive_int("LOG_BACKUP_COUNT", 14),
        database_url=database_url,
        app_env=os.getenv("APP_ENV", "development"),
        timezone=os.getenv("APP_TIMEZONE", "Asia/Hong_Kong"),
        daily_quota=_positive_int("DAILY_QUOTA", 10),
        zhipu_api_key=os.getenv("ZHIPU_API_KEY") or None,
        zhipu_model=os.getenv("ZHIPU_MODEL", "glm-4-flash"),
        zhipu_timeout_seconds=_positive_float("ZHIPU_TIMEOUT_SECONDS", 30.0),
        zhipu_max_retries=_positive_int("ZHIPU_MAX_RETRIES", 2),
        aliyun_api_key=os.getenv("ALIYUN_API_KEY") or None,
        aliyun_embedding_model=os.getenv(
            "ALIYUN_EMBEDDING_MODEL", "qwen3.7-text-embedding"
        ),
    )
