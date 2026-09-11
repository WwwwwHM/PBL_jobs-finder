"""Database engine and transaction lifecycle management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.models.entities import Base


class Database:
    """Own the SQLAlchemy engine and provide transaction-scoped sessions."""

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.database_url
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(self.url, connect_args=connect_args)
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def initialize(self) -> None:
        """Create runtime directories and all missing tables safely."""

        if self.url == get_settings().database_url:
            get_settings().ensure_runtime_directories()
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
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
