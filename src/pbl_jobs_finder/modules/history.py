"""Authenticated, privacy-preserving summaries for the history page."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.exceptions import (
    AuthenticationError,
    InterviewAccessError,
    ResumeAccessError,
)
from pbl_jobs_finder.models.database import Database, database
from pbl_jobs_finder.models.repositories import (
    get_interview_session_for_user,
    get_recent_interview_sessions,
    get_recent_resume_records,
    get_resume_record_for_user,
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


@dataclass(frozen=True, slots=True)
class ResumeHistoryDetail:
    record_id: int
    created_at: str
    target_position: str
    score: int
    grade: str
    policy_version: str
    template_id: str
    missing_keywords: tuple[str, ...]
    suggestions: str
    optimized_text: str
    diagnosis: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InterviewHistoryDetail:
    session_id: int
    created_at: str
    position: str
    status: str
    question_rounds: int
    mode: str
    feedback_mode: str
    policy_version: str
    conversation: tuple[dict[str, Any], ...]
    report: dict[str, Any]


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

    def get_resume_detail(
        self, token: str, record_id: int | str | None
    ) -> ResumeHistoryDetail:
        """Return one authenticated resume result without consuming quota."""

        user = self._require_user(token)
        normalized_id = _normalize_record_id(record_id, ResumeAccessError)
        self.database.initialize()
        with self.database.session() as session:
            record = get_resume_record_for_user(
                session, normalized_id, user["phone"]
            )
            if record is None:
                raise ResumeAccessError("无权访问该简历记录")
            return ResumeHistoryDetail(
                record_id=record.id,
                created_at=self._format_timestamp(record.created_at),
                target_position=record.target_position,
                score=record.score,
                grade=record.grade,
                policy_version=record.policy_version,
                template_id=record.template_id,
                missing_keywords=_json_string_tuple(record.missing_keywords_json),
                suggestions=record.suggestions,
                optimized_text=record.optimized_text,
                diagnosis=_json_object(record.diagnosis_json),
            )

    def get_interview_detail(
        self, token: str, session_id: int | str | None
    ) -> InterviewHistoryDetail:
        """Return one authenticated interview transcript and saved report."""

        user = self._require_user(token)
        normalized_id = _normalize_record_id(session_id, InterviewAccessError)
        self.database.initialize()
        with self.database.session() as session:
            interview = get_interview_session_for_user(
                session, normalized_id, user["phone"]
            )
            if interview is None:
                raise InterviewAccessError("无权访问该面试会话")
            return InterviewHistoryDetail(
                session_id=interview.id,
                created_at=self._format_timestamp(interview.created_at),
                position=interview.position,
                status=interview.status,
                question_rounds=interview.question_rounds,
                mode=interview.mode,
                feedback_mode=interview.feedback_mode,
                policy_version=interview.policy_version,
                conversation=_json_object_tuple(interview.conversation_json),
                report=_json_object(interview.report),
            )

    def _require_user(self, token: str) -> dict[str, str]:
        user = self._verify_token((token or "").strip())
        if user is None:
            raise AuthenticationError("登录已失效，请重新登录")
        return user

    def _format_timestamp(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone).strftime("%Y-%m-%d %H:%M")


history_service = HistoryService()


def _normalize_record_id(
    value: int | str | None,
    error_type: type[InterviewAccessError | ResumeAccessError],
) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise error_type("无权访问该历史记录") from exc
    if normalized <= 0:
        raise error_type("无权访问该历史记录")
    return normalized


def _json_object(serialized: str) -> dict[str, Any]:
    try:
        value = json.loads(serialized or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_string_tuple(serialized: str) -> tuple[str, ...]:
    try:
        value = json.loads(serialized or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _json_object_tuple(serialized: str) -> tuple[dict[str, Any], ...]:
    try:
        value = json.loads(serialized or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


__all__ = [
    "MAX_HISTORY_ITEMS",
    "HistoryService",
    "HistorySnapshot",
    "InterviewHistoryDetail",
    "InterviewHistoryItem",
    "ResumeHistoryDetail",
    "ResumeHistoryItem",
    "history_service",
]
