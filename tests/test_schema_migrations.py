"""Database migration coverage for the M1 policy metadata upgrade."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect, text

from pbl_jobs_finder.models.database import Database


class SchemaMigrationTests(unittest.TestCase):
    def test_existing_sqlite_tables_are_upgraded_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy.db"
            database = Database(f"sqlite:///{database_path.as_posix()}")
            try:
                with database.engine.begin() as connection:
                    connection.execute(
                        text("CREATE TABLE resume_records (id INTEGER PRIMARY KEY)")
                    )
                    connection.execute(
                        text("CREATE TABLE interview_sessions (id INTEGER PRIMARY KEY)")
                    )
                    connection.execute(
                        text("INSERT INTO resume_records (id) VALUES (7)")
                    )
                    connection.execute(
                        text("INSERT INTO interview_sessions (id) VALUES (9)")
                    )

                database.initialize()
                database.initialize()

                inspector = inspect(database.engine)
                resume_columns = {
                    item["name"] for item in inspector.get_columns("resume_records")
                }
                interview_columns = {
                    item["name"] for item in inspector.get_columns("interview_sessions")
                }
                self.assertTrue(
                    {"policy_version", "grade", "diagnosis_json", "template_id", "photo_data_uri"}
                    <= resume_columns
                )
                self.assertTrue(
                    {
                        "mode",
                        "feedback_mode",
                        "policy_version",
                        "question_plan_json",
                        "difficulty",
                    }
                    <= interview_columns
                )

                with database.engine.connect() as connection:
                    resume = connection.execute(
                        text(
                            "SELECT id, policy_version, grade, diagnosis_json, "
                            "template_id, photo_data_uri FROM resume_records"
                        )
                    ).one()
                    interview = connection.execute(
                        text(
                            "SELECT id, mode, feedback_mode, policy_version, "
                            "question_plan_json, difficulty FROM interview_sessions"
                        )
                    ).one()
                    migration_count = connection.execute(
                        text(
                            "SELECT COUNT(*) FROM schema_migrations "
                            "WHERE version = '2026-09-21-m1-policy-metadata'"
                        )
                    ).scalar_one()

                self.assertEqual(tuple(resume), (7, "legacy-v1", "", "{}", "classic", ""))
                self.assertEqual(
                    tuple(interview),
                    (9, "standard_live", "live", "interview-standard-v1", "[]", "standard"),
                )
                self.assertEqual(migration_count, 1)
            finally:
                database.dispose()

    def test_new_database_contains_policy_metadata_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "new.db"
            database = Database(f"sqlite:///{database_path.as_posix()}")
            try:
                database.initialize()
                inspector = inspect(database.engine)
                self.assertIn("schema_migrations", inspector.get_table_names())
                self.assertIn(
                    "diagnosis_json",
                    {item["name"] for item in inspector.get_columns("resume_records")},
                )
                self.assertIn(
                    "question_plan_json",
                    {
                        item["name"]
                        for item in inspector.get_columns("interview_sessions")
                    },
                )
            finally:
                database.dispose()


if __name__ == "__main__":
    unittest.main()
