"""Application logging behavior and privacy tests."""

from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from pbl_jobs_finder.exceptions import ResumeParseError
from pbl_jobs_finder.utils.logging import (
    configure_logging,
    report_exception,
    shutdown_logging,
)


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        shutdown_logging()

    def test_error_log_contains_traceback_and_redacts_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                log_path = configure_logging(
                    log_dir=temp_dir,
                    log_level="DEBUG",
                    backup_count=3,
                    timezone_name="Asia/Hong_Kong",
                    sensitive_values=("provider-secret-123",),
                )
                logger = logging.getLogger("pbl_jobs_finder.tests.logging")

                try:
                    raise RuntimeError(
                        "api_key=provider-secret-123 token=session-secret-456 "
                        "phone=13800138000 email=user@example.com"
                    )
                except RuntimeError as exc:
                    error_id = report_exception(
                        logger,
                        "test.failure",
                        exc,
                        record_id=42,
                    )

                for handler in logging.getLogger().handlers:
                    handler.flush()
                content = Path(log_path).read_text(encoding="utf-8")

                self.assertIn(error_id, content)
                self.assertIn("operation=test.failure", content)
                self.assertIn("record_id=42", content)
                self.assertIn("Traceback", content)
                self.assertIn("api_key=<redacted>", content)
                self.assertIn("token=<redacted>", content)
                self.assertIn("1**********", content)
                self.assertIn("<redacted-email>", content)
                self.assertNotIn("provider-secret-123", content)
                self.assertNotIn("session-secret-456", content)
                self.assertNotIn("13800138000", content)
                self.assertNotIn("user@example.com", content)
            finally:
                shutdown_logging()

    def test_repeated_configuration_does_not_duplicate_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                first = configure_logging(log_dir=temp_dir, timezone_name="UTC")
                second = configure_logging(log_dir=temp_dir, timezone_name="UTC")
                managed_handlers = [
                    handler
                    for handler in logging.getLogger().handlers
                    if getattr(handler, "_pbl_jobs_finder_handler", False)
                ]

                self.assertEqual(first, second)
                self.assertEqual(len(managed_handlers), 2)
            finally:
                shutdown_logging()

    def test_custom_exception_log_contains_structured_error_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                log_path = configure_logging(log_dir=temp_dir, timezone_name="UTC")
                logger = logging.getLogger("pbl_jobs_finder.tests.logging")

                try:
                    raise ResumeParseError("PDF 内容无效")
                except ResumeParseError as exc:
                    report_exception(logger, "resume.parse", exc)

                for handler in logging.getLogger().handlers:
                    handler.flush()
                content = Path(log_path).read_text(encoding="utf-8")

                self.assertIn("code=13005", content)
                self.assertIn("description='上传的简历解析失败'", content)
                self.assertIn("message='PDF 内容无效'", content)
            finally:
                shutdown_logging()

    def test_invalid_runtime_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "Unsupported log level"):
                configure_logging(log_dir=temp_dir, log_level="VERBOSE")
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                configure_logging(log_dir=temp_dir, backup_count=0)


    def test_locked_rollover_preserves_records_and_retries_after_delay(self) -> None:
        for winerror in (32, 33):
            with self.subTest(winerror=winerror), tempfile.TemporaryDirectory() as temp_dir:
                try:
                    log_path = configure_logging(log_dir=temp_dir, timezone_name="UTC")
                    handler = next(
                        item for item in logging.getLogger().handlers
                        if isinstance(item, TimedRotatingFileHandler)
                    )
                    logger = logging.getLogger("pbl_jobs_finder.tests.logging")
                    handler.rolloverAt = int(time.time()) - 1
                    original_rollover = handler.rolloverAt
                    locked = PermissionError("Log file is in use")
                    locked.winerror = winerror

                    with (
                        patch.object(handler, "rotate", side_effect=locked) as rotate,
                        patch.object(handler, "handleError") as handle_error,
                        patch("pbl_jobs_finder.utils.logging.time.monotonic") as clock,
                    ):
                        clock.return_value = 100
                        logger.info("record during lock")
                        clock.return_value = 159
                        logger.info("record during retry delay")
                        self.assertEqual(rotate.call_count, 1)
                        self.assertEqual(handler.rolloverAt, original_rollover)
                        content = log_path.read_text(encoding="utf-8")
                        self.assertIn("record during lock", content)
                        self.assertIn("record during retry delay", content)

                        rotate.side_effect = os.rename
                        clock.return_value = 160
                        logger.info("record after unlock")
                        self.assertEqual(rotate.call_count, 2)
                        handle_error.assert_not_called()

                    archives = list(Path(temp_dir).glob("app.log.*"))
                    self.assertEqual(len(archives), 1)
                    self.assertIn("record during lock", archives[0].read_text(encoding="utf-8"))
                    self.assertIn("record after unlock", log_path.read_text(encoding="utf-8"))
                    self.assertGreater(handler.rolloverAt, original_rollover)
                finally:
                    shutdown_logging()

    @unittest.skipUnless(os.name == "nt", "Windows file sharing semantics")
    def test_real_windows_file_lock_during_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                log_path = Path(temp_dir) / "app.log"
                log_path.write_text("existing log\n", encoding="utf-8")
                yesterday = time.time() - 86400
                os.utime(log_path, (yesterday, yesterday))
                with log_path.open("a", encoding="utf-8"), patch.object(
                    TimedRotatingFileHandler, "handleError"
                ) as handle_error:
                    configure_logging(log_dir=temp_dir, timezone_name="UTC")
                    logging.getLogger(__name__).info("startup with occupied log")
                    handle_error.assert_not_called()
                content = log_path.read_text(encoding="utf-8")
                self.assertIn("existing log", content)
                self.assertIn("Logging initialized", content)
                self.assertIn("startup with occupied log", content)
            finally:
                shutdown_logging()

    def test_unrelated_permission_error_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                configure_logging(log_dir=temp_dir, timezone_name="UTC")
                handler = next(
                    item for item in logging.getLogger().handlers
                    if isinstance(item, TimedRotatingFileHandler)
                )
                with patch.object(handler, "rotate", side_effect=PermissionError("denied")):
                    with self.assertRaisesRegex(PermissionError, "denied"):
                        handler.doRollover()
            finally:
                shutdown_logging()


if __name__ == "__main__":
    unittest.main()
