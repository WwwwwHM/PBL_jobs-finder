"""Central application logging with rotation and sensitive-value redaction."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pbl_jobs_finder.config import get_settings

_MANAGED_HANDLER = "_pbl_jobs_finder_handler"
_PHONE_PATTERN = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_PASSWORD_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:\s/]+:)([^@\s/]+)(@)")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|"
    r"authorization)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


class _SafeFormatter(logging.Formatter):
    """Format in the configured timezone and redact common credentials and PII."""

    def __init__(
        self,
        fmt: str,
        *,
        timezone_name: str,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        super().__init__(fmt=fmt)
        self.timezone = ZoneInfo(timezone_name)
        self.sensitive_values = tuple(
            value for value in sensitive_values if value and len(value) >= 6
        )

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created, self.timezone)
        return timestamp.strftime(datefmt or "%Y-%m-%d %H:%M:%S%z")

    def format(self, record: logging.LogRecord) -> str:
        return self._redact(super().format(record))

    def _redact(self, value: str) -> str:
        for secret in self.sensitive_values:
            value = value.replace(secret, "<redacted>")
        value = _BEARER_PATTERN.sub(r"\1<redacted>", value)
        value = _URL_PASSWORD_PATTERN.sub(r"\1<redacted>\3", value)
        value = _CREDENTIAL_PATTERN.sub(r"\1\2<redacted>", value)
        value = _PHONE_PATTERN.sub("1**********", value)
        return _EMAIL_PATTERN.sub("<redacted-email>", value)


def configure_logging(
    *,
    log_dir: str | Path | None = None,
    log_level: str | None = None,
    backup_count: int | None = None,
    timezone_name: str | None = None,
    sensitive_values: tuple[str, ...] | None = None,
) -> Path:
    """Configure console and daily rotating file logs, returning the log path.

    Repeated calls with the same configuration are safe. A changed configuration
    replaces only handlers installed by this application.
    """

    settings = get_settings()
    resolved_dir = Path(log_dir or settings.log_dir).resolve()
    resolved_level_name = (log_level or settings.log_level).strip().upper()
    resolved_level = logging.getLevelNamesMapping().get(resolved_level_name)
    if resolved_level is None:
        raise ValueError(f"Unsupported log level: {resolved_level_name}")
    resolved_backup_count = (
        settings.log_backup_count if backup_count is None else backup_count
    )
    if resolved_backup_count <= 0:
        raise ValueError("backup_count must be greater than zero")
    resolved_timezone = timezone_name or settings.timezone
    log_path = resolved_dir / "app.log"
    signature = (
        str(log_path),
        resolved_level,
        resolved_backup_count,
        resolved_timezone,
    )

    root_logger = logging.getLogger()
    managed = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, _MANAGED_HANDLER, False)
    ]
    if len(managed) == 2 and all(
        getattr(handler, "_pbl_signature", None) == signature for handler in managed
    ):
        return log_path

    shutdown_logging()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    configured_secrets = sensitive_values or tuple(
        value for value in (settings.zhipu_api_key, settings.aliyun_api_key) if value
    )
    formatter = _SafeFormatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
        timezone_name=resolved_timezone,
        sensitive_values=configured_secrets,
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(formatter)
    _mark_managed(console_handler, signature)

    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=resolved_backup_count,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(formatter)
    file_handler.suffix = "%Y-%m-%d"
    _mark_managed(file_handler, signature)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(resolved_level)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info(
        "Logging initialized level=%s file=%s retention_days=%s",
        resolved_level_name,
        log_path,
        resolved_backup_count,
    )
    return log_path


def report_exception(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    **context: Any,
) -> str:
    """Log a failure with its traceback and return a user-facing correlation ID."""

    error_id = uuid4().hex[:12]
    context_text = " ".join(
        f"{key}={value!r}" for key, value in sorted(context.items())
    )
    logger.error(
        "Operation failed error_id=%s operation=%s exception=%s%s",
        error_id,
        operation,
        type(error).__name__,
        f" {context_text}" if context_text else "",
        exc_info=_exception_info(error),
    )
    return error_id


def shutdown_logging() -> None:
    """Remove and close handlers installed by :func:`configure_logging`."""

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, _MANAGED_HANDLER, False):
            root_logger.removeHandler(handler)
            handler.close()
    logging.captureWarnings(False)


def _exception_info(
    error: BaseException,
) -> tuple[type[BaseException], BaseException, TracebackType | None]:
    return type(error), error, error.__traceback__


def _mark_managed(handler: logging.Handler, signature: tuple[object, ...]) -> None:
    handler._pbl_jobs_finder_handler = True  # type: ignore[attr-defined]
    handler._pbl_signature = signature  # type: ignore[attr-defined]


__all__ = ["configure_logging", "report_exception", "shutdown_logging"]
