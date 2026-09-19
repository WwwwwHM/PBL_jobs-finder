"""Environment-backed application configuration tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pbl_jobs_finder.config.settings import PROJECT_ROOT, get_settings


class SettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_blank_optional_paths_use_data_directory_defaults(self) -> None:
        environment = {
            "DATA_DIR": "",
            "UPLOADS_DIR": "",
            "EXPORTS_DIR": "",
            "CHROMA_DIR": "",
            "LOG_DIR": "",
        }
        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            settings = get_settings()

        expected_data = (PROJECT_ROOT / "data").resolve()
        self.assertEqual(settings.data_dir, expected_data)
        self.assertEqual(settings.uploads_dir, expected_data / "uploads")
        self.assertEqual(settings.exports_dir, expected_data / "exports")
        self.assertEqual(settings.chroma_dir, expected_data / "chroma_db")
        self.assertEqual(settings.log_dir, expected_data / "logs")

    def test_blank_child_paths_follow_custom_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            environment = {
                "DATA_DIR": temp_dir,
                "UPLOADS_DIR": "",
                "EXPORTS_DIR": "",
                "CHROMA_DIR": "",
                "LOG_DIR": "",
            }
            with patch.dict(os.environ, environment):
                get_settings.cache_clear()
                settings = get_settings()

            data_dir = Path(temp_dir).resolve()
            self.assertEqual(settings.data_dir, data_dir)
            self.assertEqual(settings.uploads_dir, data_dir / "uploads")
            self.assertEqual(settings.exports_dir, data_dir / "exports")
            self.assertEqual(settings.chroma_dir, data_dir / "chroma_db")
            self.assertEqual(settings.log_dir, data_dir / "logs")


if __name__ == "__main__":
    unittest.main()
