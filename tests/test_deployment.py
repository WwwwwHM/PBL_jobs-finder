"""Deployment preflight regression tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect

from pbl_jobs_finder.config import Settings, get_settings
from scripts.check_deployment import (
    EXPECTED_TABLES,
    check_api_keys,
    check_database,
    check_question_source,
    check_runtime_directories,
    run_preflight,
)


def _settings(root: Path, *, with_keys: bool = True) -> Settings:
    data_dir = root / "data"
    return Settings(
        project_root=root,
        data_dir=data_dir,
        uploads_dir=data_dir / "uploads",
        exports_dir=data_dir / "exports",
        chroma_dir=data_dir / "chroma_db",
        log_dir=data_dir / "logs",
        log_level="INFO",
        log_backup_count=14,
        database_url=f"sqlite:///{(data_dir / 'candidate.db').as_posix()}",
        app_env="test",
        timezone="Asia/Hong_Kong",
        daily_quota=10,
        zhipu_api_key="configured" if with_keys else None,
        zhipu_model="glm-4-flash",
        zhipu_timeout_seconds=30,
        zhipu_max_retries=2,
        aliyun_api_key="configured" if with_keys else None,
        aliyun_embedding_model="qwen3.7-text-embedding",
        aliyun_embedding_url="https://example.invalid/embeddings",
        embedding_dimensions=1024,
        embedding_timeout_seconds=30,
        embedding_max_retries=2,
    )


class DeploymentPreflightTests(unittest.TestCase):
    def test_runtime_directories_and_database_can_initialize_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = _settings(Path(temp_dir))
            self.assertIn("writable", check_runtime_directories(settings))
            self.assertIn("4 required tables", check_database(settings))

            from sqlalchemy import create_engine

            engine = create_engine(settings.database_url)
            try:
                self.assertEqual(
                    set(inspect(engine).get_table_names()), EXPECTED_TABLES
                )
            finally:
                engine.dispose()

    def test_release_keys_are_required_without_disclosing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = _settings(Path(temp_dir), with_keys=False)
            with self.assertRaisesRegex(RuntimeError, "ZHIPU_API_KEY, ALIYUN_API_KEY"):
                check_api_keys(settings)

    def test_default_question_source_has_release_baseline(self) -> None:
        self.assertEqual(check_question_source(), "42 structured source questions")

    def test_preflight_reports_failures_instead_of_stopping_early(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = _settings(Path(temp_dir), with_keys=False)
            with (
                patch(
                    "scripts.check_deployment.get_settings", return_value=settings
                ),
                patch(
                    "scripts.check_deployment.check_chromium",
                    side_effect=RuntimeError("browser missing"),
                ),
                patch(
                    "scripts.check_deployment.check_question_index",
                    side_effect=RuntimeError("index missing"),
                ),
                patch(
                    "scripts.check_deployment.check_frontend_build",
                    return_value="built",
                ),
            ):
                get_settings.cache_clear()
                results = run_preflight(
                    require_api_keys=True,
                    require_question_index=True,
                    require_chromium=True,
                    check_frontend=True,
                )

        failures = {result.name for result in results if not result.passed}
        self.assertEqual(
            failures, {"API keys", "Question index", "Playwright Chromium"}
        )
        self.assertTrue(results[-1].passed)


if __name__ == "__main__":
    unittest.main()
