"""Authenticated, privacy-preserving summaries for the history page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.exceptions import AuthenticationError
from pbl_jobs_finder.models.database import Database, database
from pbl_jobs_finder.models.repositories import (
    get_recent_interview_sessions,
    get_recent_resume_records,
)
from pbl_jobs_finder.modules.auth import verify_token

MAX_HISTORY_ITEMS = 5


@dataclass(frozen=True, slots=True)
class ResumeHistoryItem:
    record_id: int
    created_at: str
    target_position: str
    score: int


@dataclass(frozen=True, slots=True)
class InterviewHistoryItem:
    session_id: int
    created_at: str
    position: str
    question_rounds: int
    status: str


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    resumes: tuple[ResumeHistoryItem, ...]
    interviews: tuple[InterviewHistoryItem, ...]


class HistoryService:
    """Load recent record summaries for the authenticated user only."""

    def __init__(
        self,
        database_instance: Database | None = None,
        *,
        token_verifier: Callable[[str], dict[str, str] | None] | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self.database = database_instance or database
        self._verify_token = token_verifier or verify_token
        self.timezone = ZoneInfo(timezone_name or get_settings().timezone)

    def get_recent(self, token: str) -> HistorySnapshot:
        """Return at most five records of each type without sensitive details."""

        user = self._verify_token((token or "").strip())
        if user is None:
            raise AuthenticationError("登录已失效，请重新登录")

        self.database.initialize()
        with self.database.session() as session:
            resumes = get_recent_resume_records(
                session, user["phone"], limit=MAX_HISTORY_ITEMS
            )
            interviews = get_recent_interview_sessions(
                session, user["phone"], limit=MAX_HISTORY_ITEMS
            )
            return HistorySnapshot(
                resumes=tuple(
                    ResumeHistoryItem(
                        record_id=record.id,
                        created_at=self._format_timestamp(record.created_at),
                        target_position=record.target_position,
                        score=record.score,
                    )
                    for record in resumes
                ),
                interviews=tuple(
                    InterviewHistoryItem(
                        session_id=interview.id,
                        created_at=self._format_timestamp(interview.created_at),
                        position=interview.position,
                        question_rounds=interview.question_rounds,
                        status=interview.status,
                    )
                    for interview in interviews
                ),
            )

    def _format_timestamp(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")


history_service = HistoryService()


__all__ = [
    "MAX_HISTORY_ITEMS",
    "HistoryService",
    "HistorySnapshot",
    "InterviewHistoryItem",
    "ResumeHistoryItem",
    "history_service",
]
