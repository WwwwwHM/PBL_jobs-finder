"""Fault injection at provider, persistence and download boundaries."""

import io
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import requests

from pbl_jobs_finder.exceptions import LLMServiceError, ResumeParseError
from pbl_jobs_finder.modules.file_retention import clean_expired_files
from pbl_jobs_finder.modules.file_storage import LocalFileStorage, staged_pdf
from pbl_jobs_finder.modules.resume_parser import parse_resume_pdf
from pbl_jobs_finder.utils.llm_client import ZhipuChatClient
from pbl_jobs_finder.utils.logging import (
    configure_logging,
    report_exception,
    shutdown_logging,
)
from tests import test_resume_diagnosis as resume_tests
from tests.test_embeddings import FakeSession, make_client


class ProviderStabilityTests(unittest.TestCase):
    def test_chat_retries_only_transient_failures_and_closes_transport(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        for status, calls in [(401, 1), (400, 1), (429, 3), (503, 3)]:
            error = RuntimeError("provider echoed private resume")
            error.status_code = status
            sdk = Mock()
            sdk.__enter__ = Mock(return_value=sdk)
            sdk.__exit__ = Mock(return_value=False)
            sdk.chat.completions.create.side_effect = error
            with patch("zhipuai.ZhipuAI", return_value=sdk) as factory, patch("time.sleep") as sleep:
                client = ZhipuChatClient(api_key="test", model="test", max_retries=2)
                with self.assertRaises(LLMServiceError):
                    client.complete("system", "private resume")
                self.assertEqual(sdk.chat.completions.create.call_count, calls)
                self.assertEqual(sleep.call_count, calls - 1)
                self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
                sdk.__exit__.assert_called_once()
                sdk.chat.completions.create.side_effect = [httpx.ReadTimeout("timeout"), response]
                self.assertEqual(client.complete("system", "retry input"), "ok")

    def test_embedding_invalid_url_and_auth_are_not_retried(self):
        session = FakeSession(requests.exceptions.InvalidURL("bad config"))
        with self.assertRaisesRegex(Exception, "连接失败"):
            make_client(session, retries=2).get_embedding("query")
        self.assertEqual(len(session.calls), 1)

    def test_exception_chain_does_not_log_model_or_database_payloads(self):
        server_output = io.StringIO()
        server_logger = logging.getLogger("uvicorn.error")
        server_handler = logging.StreamHandler(server_output)
        server_logger.addHandler(server_handler)
        with tempfile.TemporaryDirectory() as directory:
            try:
                path = configure_logging(log_dir=directory)
                try:
                    try:
                        raise ValueError("PRIVATE_RESUME_BODY sql parameters=[private]")
                    except ValueError as exc:
                        raise LLMServiceError("AI 服务暂时不可用") from exc
                except LLMServiceError as exc:
                    error_id = report_exception(logging.getLogger(__name__), "model.failure", exc)
                    server_logger.exception("ASGI failure")
                content = path.read_text(encoding="utf-8")
                self.assertIn(error_id, content)
                self.assertIn("ValueError", content)
                self.assertNotIn("PRIVATE_RESUME_BODY", content)
                self.assertNotIn("parameters=", content)
                self.assertNotIn("PRIVATE_RESUME_BODY", server_output.getvalue())
                self.assertNotIn("parameters=", server_output.getvalue())
            finally:
                server_logger.removeHandler(server_handler)
                server_handler.close()
                shutdown_logging()


class FileBoundaryTests(unittest.TestCase):
    def test_expiry_only_deletes_managed_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / ("1-" + "a" * 32 + ".pdf")
            fresh = root / ("2-" + "b" * 32 + ".docx")
            legacy = root / "important.pdf"
            for file in (old, fresh, legacy):
                file.write_bytes(b"%PDF-test")
            for file in (old, legacy):
                os.utime(file, (time.time() - 90000,) * 2)
            self.assertEqual(clean_expired_files(root), 1)
            self.assertTrue(fresh.exists())
            self.assertTrue(legacy.exists())

    def test_encrypted_corrupt_and_oversized_pdf_leave_no_staged_copy(self):
        from PyPDF2 import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            encrypted = root / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.encrypt("password")
            with encrypted.open("wb") as stream:
                writer.write(stream)
            corrupt = root / "corrupt.pdf"
            corrupt.write_bytes(b"%PDF-garbage")
            large = root / "large.pdf"
            large.write_bytes(b"%PDF-" + b"0" * (12 * 1024 * 1024))
            storage = LocalFileStorage(root / "uploads")
            for file in (encrypted, corrupt, large):
                with self.subTest(file=file.name), self.assertRaises(ResumeParseError), staged_pdf(storage, file) as copy:
                    parse_resume_pdf(copy)
                self.assertEqual(list(storage.root.glob("*")), [])


class ExportRollbackTests(unittest.TestCase):
    setUp = resume_tests.ResumeDiagnosisServiceTests.setUp
    tearDown = resume_tests.ResumeDiagnosisServiceTests.tearDown

    def test_word_database_failure_removes_only_new_export(self):
        result = self.service.diagnose(token="token-a", position="Python", pasted_text="Python developer with five years of backend experience")
        with (
            patch("pbl_jobs_finder.modules.resume_diagnosis.update_resume_record", side_effect=RuntimeError("db unavailable")),
            self.assertRaises(RuntimeError),
        ):
            self.service.export_optimized_resume(token="token-a", record_id=result.record_id, optimized_text="# Resume")
        self.assertEqual(list(self.service.exports_dir.glob("*.docx")), [])

    def test_pdf_database_failure_preserves_previous_export_then_recovers(self):
        result = self.service.diagnose(token="token-a", position="Python", pasted_text="Python developer with five years of backend experience")
        self.service.chat_client.response = resume_tests._resume_document_response()
        self.service.pdf_renderer = resume_tests._fake_pdf_renderer
        arguments = dict(token="token-a", record_id=result.record_id, optimized_text="# Resume")
        first = self.service.generate_pdf_resume(**arguments)
        original_bytes = first.pdf_path.read_bytes()
        with (
            patch("pbl_jobs_finder.modules.resume_diagnosis.update_resume_record", side_effect=RuntimeError("db unavailable")),
            self.assertRaises(RuntimeError),
        ):
            self.service.generate_pdf_resume(**arguments)
        self.assertEqual(list(self.service.exports_dir.glob("*.pdf")), [first.pdf_path])
        self.assertEqual(first.pdf_path.read_bytes(), original_bytes)
        recovered = self.service.generate_pdf_resume(**arguments)
        self.assertNotEqual(first.pdf_path, recovered.pdf_path)
        self.assertEqual(self.quota.status("token-a").used, 1)
