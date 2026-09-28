"""Real legacy schemas, history query plans, and application-level restore checks."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sqlalchemy import event, text

from pbl_jobs_finder.exceptions import InterviewAccessError, ResumeAccessError
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import (
    get_recent_interview_sessions,
    get_recent_resume_records,
)
from pbl_jobs_finder.modules.history import HistoryService
from scripts.backup_database import backup_sqlite

PHONES = ("13800138000", "13900139000")
LEGACY_SCHEMA = """
CREATE TABLE users (
    phone VARCHAR(11) PRIMARY KEY CHECK(length(phone) = 11),
    nickname VARCHAR(50) NOT NULL, total_usage INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE resume_records (
    id INTEGER PRIMARY KEY,
    phone VARCHAR(11) NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
    original_text TEXT NOT NULL, target_position VARCHAR(100) NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    missing_keywords_json TEXT NOT NULL, suggestions TEXT NOT NULL,
    optimized_text TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE interview_sessions (
    id INTEGER PRIMARY KEY,
    phone VARCHAR(11) NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
    position VARCHAR(100) NOT NULL, job_description TEXT NOT NULL,
    resume_text TEXT NOT NULL, status VARCHAR(20) NOT NULL,
    current_question TEXT NOT NULL, question_rounds INTEGER NOT NULL,
    follow_up_count INTEGER NOT NULL, conversation_json TEXT NOT NULL,
    report TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_resume_records_phone ON resume_records(phone);
CREATE INDEX ix_interview_sessions_phone ON interview_sessions(phone);
"""


def create_legacy_database(path: Path) -> None:
    """Populate pre-policy tables independently of today's ORM definitions."""
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(LEGACY_SCHEMA)
        for phone in PHONES:
            connection.execute(
                "INSERT INTO users (phone, nickname, total_usage) VALUES (?, ?, ?)",
                (phone, "测试用户", 7),
            )
            for index in range(7):
                connection.execute(
                    "INSERT INTO resume_records (phone, original_text, target_position, "
                    "score, missing_keywords_json, suggestions, optimized_text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        phone,
                        f"原始简历 {index}",
                        "后端工程师",
                        70 + index,
                        '["SQL"]',
                        "补充成果",
                        f"优化稿 {index}",
                    ),
                )
                connection.execute(
                    "INSERT INTO interview_sessions (phone, position, job_description, "
                    "resume_text, status, current_question, question_rounds, follow_up_count, "
                    "conversation_json, report) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        phone,
                        "后端工程师",
                        "SQL",
                        f"简历 {index}",
                        "completed",
                        "",
                        5,
                        0,
                        json.dumps([{"role": "user", "content": f"回答 {index}"}]),
                        json.dumps(
                            {"overall_score": 80 + index, "summary": "已有报告"}
                        ),
                    ),
                )
        # All rows share a timestamp so ordering must also use the record ID.
        for table in ("users", "resume_records", "interview_sessions"):
            connection.execute(
                f"UPDATE {table} SET created_at='2026-09-20 10:00:00', "
                "updated_at='2026-09-20 10:00:00'"
            )
        connection.commit()


def snapshot(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as connection:
        result = {}
        for table in ("users", "resume_records", "interview_sessions"):
            cursor = connection.execute(f"SELECT * FROM {table} ORDER BY 1")
            result[table] = (
                [item[0] for item in cursor.description],
                cursor.fetchall(),
            )
        return result


class DataStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "legacy.db"
        create_legacy_database(self.path)
        self.database = Database(f"sqlite:///{self.path.as_posix()}")
        self.addCleanup(self.database.dispose)

    def assert_original_rows(self, expected, database):
        with database.engine.connect() as connection:
            for table, (columns, rows) in expected.items():
                actual = connection.execute(
                    text(f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1")
                ).all()
                self.assertEqual([tuple(row) for row in actual], rows, table)
            self.assertEqual(
                connection.execute(text("PRAGMA integrity_check")).scalar(), "ok"
            )
            self.assertEqual(
                connection.execute(text("PRAGMA foreign_key_check")).all(), []
            )

    def assert_history(self, database):
        service = HistoryService(
            database,
            token_verifier=lambda token: {"phone": token} if token in PHONES else None,
        )
        for offset, phone in enumerate(PHONES):
            history = service.get_recent(phone)
            expected_ids = list(range(7 + offset * 7, 2 + offset * 7, -1))
            self.assertEqual([row.record_id for row in history.resumes], expected_ids)
            self.assertEqual(
                [row.session_id for row in history.interviews], expected_ids
            )
            for row in history.resumes:
                detail = service.get_resume_detail(phone, row.record_id)
                self.assertEqual(
                    detail.optimized_text, f"优化稿 {(row.record_id - 1) % 7}"
                )
                self.assertEqual(detail.policy_version, "legacy-v1")
                self.assertEqual(detail.diagnosis, {})
                self.assertEqual(detail.missing_keywords, ("SQL",))
            for row in history.interviews:
                detail = service.get_interview_detail(phone, row.session_id)
                index = (row.session_id - 1) % 7
                self.assertEqual(detail.conversation[0]["content"], f"回答 {index}")
                self.assertEqual(detail.report["overall_score"], 80 + index)
                self.assertEqual(detail.mode, "standard_live")
                self.assertEqual(detail.difficulty, "standard")
        with self.assertRaises(ResumeAccessError):
            service.get_resume_detail(PHONES[0], 14)
        with self.assertRaises(InterviewAccessError):
            service.get_interview_detail(PHONES[1], 1)

    def test_legacy_upgrade_preserves_every_field_and_authenticated_history(self):
        before = snapshot(self.path)
        self.database.initialize()
        self.database.initialize()
        self.assert_original_rows(before, self.database)
        self.assert_history(self.database)
        with self.database.engine.connect() as connection:
            versions = connection.execute(
                text("SELECT version FROM schema_migrations")
            ).all()
            self.assertEqual(len(versions), 4)
            self.assertIn("2026-09-28-password-auth", {row[0] for row in versions})

    def test_repository_queries_use_composite_indexes_without_temporary_sort(self):
        for database in (
            self.database,
            Database(f"sqlite:///{self.root.as_posix()}/new.db"),
        ):
            self.addCleanup(database.dispose)
            database.initialize()
            statements = []

            def capture(
                connection, cursor, statement, parameters, context, executemany
            ):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append((statement, parameters))

            event.listen(database.engine, "before_cursor_execute", capture)
            try:
                with database.session() as session:
                    get_recent_resume_records(session, PHONES[0])
                    get_recent_interview_sessions(session, PHONES[0])
            finally:
                event.remove(database.engine, "before_cursor_execute", capture)
            self.assertEqual(len(statements), 2)
            with database.engine.connect() as connection:
                for table, (statement, parameters) in zip(
                    ("resume_records", "interview_sessions"), statements
                ):
                    plan = " ".join(
                        row[3]
                        for row in connection.exec_driver_sql(
                            "EXPLAIN QUERY PLAN " + statement, parameters
                        )
                    )
                    self.assertIn(f"USING INDEX ix_{table}_user_history", plan)
                    self.assertNotIn("TEMP B-TREE", plan)

    def test_wal_backup_restores_complete_history_in_independent_directory(self):
        self.database.initialize()
        backup_path = self.root / "snapshots" / "backup.db"
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute(
                "UPDATE users SET nickname='WAL中的用户' WHERE phone=?", (PHONES[0],)
            )
            writer.commit()
            self.assertGreater(Path(str(self.path) + "-wal").stat().st_size, 0)
            before = snapshot(self.path)
            backup_sqlite(self.database.url, backup_path)
            # A later write must not leak into the snapshot.
            writer.execute(
                "UPDATE users SET nickname='备份后的用户' WHERE phone=?", (PHONES[0],)
            )
            writer.commit()
        restore_path = self.root / "restored" / "app.db"
        restore_path.parent.mkdir()
        shutil.copy2(backup_path, restore_path)
        restored = Database(f"sqlite:///{restore_path.as_posix()}")
        self.addCleanup(restored.dispose)
        restored.initialize()
        self.assert_original_rows(before, restored)
        self.assert_history(restored)
        with restored.session() as session:
            session.execute(text("UPDATE users SET nickname='独立恢复库'"))
        with self.database.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT nickname FROM users WHERE phone=:phone"),
                    {"phone": PHONES[0]},
                ).scalar(),
                "备份后的用户",
            )


if __name__ == "__main__":
    unittest.main()
