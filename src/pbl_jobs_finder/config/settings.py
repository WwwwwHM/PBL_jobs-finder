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


def _retry_count(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 0 <= value <= 5:
        raise ValueError(f"{name} must be between zero and five")
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


def _boolean(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _configured_path(name: str, default: Path) -> Path:
    """Resolve an optional path setting, treating blank dotenv values as unset."""

    configured = os.getenv(name, "").strip()
    return Path(configured or default).resolve()


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
    aliyun_embedding_url: str
    embedding_dimensions: int
    embedding_timeout_seconds: float
    embedding_max_retries: int
    resume_policy_version: str = "legacy-v1"
    interview_policy_version: str = "interview-standard-v1"
    enable_resume_dimensions: bool = False
    enable_resume_templates: bool = False
    enable_interview_modes: bool = False
    state_backend: str = "memory"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_prefix: str = "pbl-jobs-finder:"

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

    data_dir = _configured_path("DATA_DIR", PROJECT_ROOT / "data")
    uploads_dir = _configured_path("UPLOADS_DIR", data_dir / "uploads")
    exports_dir = _configured_path("EXPORTS_DIR", data_dir / "exports")
    chroma_dir = _configured_path("CHROMA_DIR", data_dir / "chroma_db")
    log_dir = _configured_path("LOG_DIR", data_dir / "logs")
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(
            "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL"
        )
    default_database_url = f"sqlite:///{(data_dir / 'job_assistant.db').as_posix()}"
    database_url = os.getenv("DATABASE_URL", "").strip() or default_database_url
    state_backend = os.getenv("STATE_BACKEND", "memory").strip().lower()
    if state_backend not in {"memory", "redis"}:
        raise ValueError("STATE_BACKEND must be memory or redis")
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    redis_prefix = os.getenv("REDIS_PREFIX", "pbl-jobs-finder:").strip()
    if state_backend == "redis" and not redis_url.startswith(("redis://", "rediss://")):
        raise ValueError("REDIS_URL must use redis:// or rediss://")
    if not redis_prefix or any(char in redis_prefix for char in "*?[]"):
        raise ValueError("REDIS_PREFIX must be non-empty and contain no glob characters")

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
        zhipu_max_retries=_retry_count("ZHIPU_MAX_RETRIES", 2),
        aliyun_api_key=os.getenv("ALIYUN_API_KEY") or None,
        aliyun_embedding_model=os.getenv(
            "ALIYUN_EMBEDDING_MODEL", "qwen3.7-text-embedding"
        ),
        aliyun_embedding_url=os.getenv(
            "ALIYUN_EMBEDDING_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        ).strip(),
        embedding_dimensions=_positive_int("EMBEDDING_DIMENSIONS", 1024),
        embedding_timeout_seconds=_positive_float(
            "EMBEDDING_TIMEOUT_SECONDS", 30.0
        ),
        embedding_max_retries=_retry_count("EMBEDDING_MAX_RETRIES", 2),
        resume_policy_version=(
            os.getenv("RESUME_POLICY_VERSION", "legacy-v1").strip() or "legacy-v1"
        ),
        interview_policy_version=(
            os.getenv("INTERVIEW_POLICY_VERSION", "interview-standard-v1").strip()
            or "interview-standard-v1"
        ),
        enable_resume_dimensions=_boolean("ENABLE_RESUME_DIMENSIONS"),
        enable_resume_templates=_boolean("ENABLE_RESUME_TEMPLATES"),
        enable_interview_modes=_boolean("ENABLE_INTERVIEW_MODES"),
        state_backend=state_backend,
        redis_url=redis_url,
        redis_prefix=redis_prefix,
    )
