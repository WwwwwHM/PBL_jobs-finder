"""Application logging behavior and privacy tests."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
