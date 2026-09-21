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

    def test_m1_policy_features_are_disabled_by_default(self) -> None:
        environment = {
            "RESUME_POLICY_VERSION": "",
            "INTERVIEW_POLICY_VERSION": "",
            "ENABLE_RESUME_DIMENSIONS": "",
            "ENABLE_RESUME_TEMPLATES": "",
            "ENABLE_INTERVIEW_MODES": "",
        }
        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.resume_policy_version, "legacy-v1")
        self.assertEqual(settings.interview_policy_version, "interview-standard-v1")
        self.assertFalse(settings.enable_resume_dimensions)
        self.assertFalse(settings.enable_resume_templates)
        self.assertFalse(settings.enable_interview_modes)

    def test_policy_feature_flags_accept_explicit_boolean_values(self) -> None:
        environment = {
            "RESUME_POLICY_VERSION": "resume-general-v1",
            "ENABLE_RESUME_DIMENSIONS": "true",
            "ENABLE_RESUME_TEMPLATES": "1",
            "ENABLE_INTERVIEW_MODES": "yes",
        }
        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.resume_policy_version, "resume-general-v1")
        self.assertTrue(settings.enable_resume_dimensions)
        self.assertTrue(settings.enable_resume_templates)
        self.assertTrue(settings.enable_interview_modes)

    def test_invalid_policy_feature_flag_is_rejected(self) -> None:
        with patch.dict(os.environ, {"ENABLE_RESUME_DIMENSIONS": "sometimes"}):
            get_settings.cache_clear()
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                get_settings()


if __name__ == "__main__":
    unittest.main()
