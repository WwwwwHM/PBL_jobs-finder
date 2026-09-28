"""Database engine and transaction lifecycle management."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from threading import RLock

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.models.entities import Base


class Database:
    """Own the SQLAlchemy engine and provide transaction-scoped sessions."""

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.database_url
        connect_args = (
            {"check_same_thread": False, "timeout": 30}
            if self.url.startswith("sqlite")
            else {}
        )
        self.engine: Engine = create_engine(self.url, connect_args=connect_args)
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
        self._initialize_lock = RLock()

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    def initialize(self) -> None:
        """Create tables and apply idempotent SQLite compatibility migrations."""

        with self._initialize_lock:
            if self.url == get_settings().database_url:
                get_settings().ensure_runtime_directories()
            Base.metadata.create_all(self.engine)
            if self.url.startswith("sqlite"):
                self._apply_sqlite_migrations()

    def _apply_sqlite_migrations(self) -> None:
        migration_version = "2026-09-21-m1-policy-metadata"
        required_columns = {
            "resume_records": {
                "photo_data_uri": "photo_data_uri TEXT NOT NULL DEFAULT ''",
                "policy_version": (
                    "policy_version VARCHAR(64) NOT NULL DEFAULT 'legacy-v1'"
                ),
                "grade": "grade VARCHAR(8) NOT NULL DEFAULT ''",
                "diagnosis_json": "diagnosis_json TEXT NOT NULL DEFAULT '{}'",
                "template_id": (
                    "template_id VARCHAR(64) NOT NULL DEFAULT 'classic'"
                ),
            },
            "interview_sessions": {
                "difficulty": "difficulty VARCHAR(16) NOT NULL DEFAULT 'standard'",
                "mode": "mode VARCHAR(32) NOT NULL DEFAULT 'standard_live'",
                "feedback_mode": (
                    "feedback_mode VARCHAR(16) NOT NULL DEFAULT 'live'"
                ),
                "policy_version": (
                    "policy_version VARCHAR(64) NOT NULL "
                    "DEFAULT 'interview-standard-v1'"
                ),
                "question_plan_json": (
                    "question_plan_json TEXT NOT NULL DEFAULT '[]'"
                ),
            },
        }
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            for table_name, columns in required_columns.items():
                if table_name not in table_names:
                    continue
                existing = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
                for column_name, definition in columns.items():
                    if column_name in existing:
                        continue
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
                    )
                    existing.add(column_name)
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO schema_migrations (version) "
                    "VALUES (:version)"
                ),
                {"version": migration_version},
            )
            connection.execute(
                text("INSERT OR IGNORE INTO schema_migrations (version) VALUES (:version)"),
                {"version": "2026-09-22-interview-difficulty"},
            )
            connection.execute(
                text("INSERT OR IGNORE INTO schema_migrations (version) VALUES (:version)"),
                {"version": "2026-09-23-resume-photo"},
            )
            for table_name in ("resume_records", "interview_sessions"):
                columns = {item["name"] for item in inspect(connection).get_columns(table_name)}
                if {"phone", "created_at", "id"} <= columns:
                    connection.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table_name}_user_history "
                        f"ON {table_name} (phone, created_at, id)"
                    ))

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Commit a unit of work or roll it back when an exception occurs."""

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


database = Database()
