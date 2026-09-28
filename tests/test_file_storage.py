"""Upload validation and cleanup at the storage boundary."""

import tempfile
import unittest
from pathlib import Path

from pbl_jobs_finder.exceptions import ResumeParseError
from pbl_jobs_finder.modules.file_storage import LocalFileStorage, staged_pdf
from pbl_jobs_finder.modules.resume_parser import parse_resume_pdf
from tests.test_resume_diagnosis import _write_text_pdf


class FileStorageTests(unittest.TestCase):
    def test_copy_read_delete_and_exception_cleanup_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.pdf"
            _write_text_pdf(source, "Python developer")
            storage = LocalFileStorage(root / "uploads")
            key = storage.save_pdf(source)
            with storage.materialize(key) as local:
                self.assertEqual(local.read_bytes(), source.read_bytes())
                self.assertIn("Python developer", parse_resume_pdf(local))
            storage.delete(key)
            storage.delete(key)
            with (
                self.assertRaisesRegex(RuntimeError, "parse failed"),
                staged_pdf(storage, source),
            ):
                raise RuntimeError("parse failed")
            self.assertTrue(source.exists())
            self.assertEqual(list(storage.root.iterdir()), [])

    def test_invalid_upload_and_unsafe_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalFileStorage(root / "uploads")
            for name, content in [("empty.pdf", b""), ("fake.pdf", b"not pdf"),
                                  ("resume.txt", b"%PDF-1.4")]:
                source = root / name
                source.write_bytes(content)
                with self.subTest(name=name), self.assertRaises(ResumeParseError):
                    storage.save_pdf(source)
            for key in ["../resume.pdf", "C:/secret.pdf", "/tmp/private.pdf", "x.pdf"]:
                with self.subTest(key=key), self.assertRaises(ResumeParseError):
                    storage.delete(key)

    def test_large_page_count_is_rejected_before_text_extraction(self):
        from PyPDF2 import PdfWriter

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "many-pages.pdf"
            writer = PdfWriter()
            for _ in range(101):
                writer.add_blank_page(width=72, height=72)
            with source.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ResumeParseError, "100"):
                parse_resume_pdf(source)
