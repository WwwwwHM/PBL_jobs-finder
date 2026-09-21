"""CRUD operations for the application's persistent entities."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pbl_jobs_finder.models.entities import InterviewSession, ResumeRecord, User


def get_user(session: Session, phone: str) -> User | None:
    return session.get(User, phone)


def get_or_create_user(session: Session, phone: str, nickname: str = "求职者") -> User:
    user = get_user(session, phone)
    if user is None:
        try:
            with session.begin_nested():
                user = User(phone=phone, nickname=nickname)
                session.add(user)
                session.flush()
        except IntegrityError:
            # Another request may have created this phone after our first read.
            user = get_user(session, phone)
            if user is None:
                raise
    return user


def update_user(session: Session, phone: str, **changes: Any) -> User:
    user = _require(session, User, phone)
    _apply_changes(user, changes, {"nickname", "total_usage"})
    session.flush()
    return user


def delete_user(session: Session, phone: str) -> None:
    session.delete(_require(session, User, phone))


def create_resume_record(
    session: Session,
    *,
    phone: str,
    original_text: str,
    target_position: str,
    score: int,
    missing_keywords: list[str],
    suggestions: str,
    optimized_text: str,
    policy_version: str = "legacy-v1",
    grade: str = "",
    diagnosis: dict[str, Any] | None = None,
    template_id: str = "classic",
) -> ResumeRecord:
    get_or_create_user(session, phone)
    record = ResumeRecord(
        phone=phone,
        original_text=original_text,
        target_position=target_position,
        score=score,
        missing_keywords_json=json.dumps(missing_keywords, ensure_ascii=False),
        suggestions=suggestions,
        optimized_text=optimized_text,
        policy_version=policy_version,
        grade=grade,
        diagnosis_json=json.dumps(diagnosis or {}, ensure_ascii=False),
        template_id=template_id,
    )
    session.add(record)
    session.flush()
    return record


def update_resume_record(session: Session, record_id: int, **changes: Any) -> ResumeRecord:
    record = _require(session, ResumeRecord, record_id)
    if "missing_keywords" in changes:
        changes["missing_keywords_json"] = json.dumps(
            changes.pop("missing_keywords"), ensure_ascii=False
        )
    if "diagnosis" in changes:
        changes["diagnosis_json"] = json.dumps(
            changes.pop("diagnosis"), ensure_ascii=False
        )
    allowed = {
        "original_text",
        "target_position",
        "score",
        "missing_keywords_json",
        "suggestions",
        "optimized_text",
        "policy_version",
        "grade",
        "diagnosis_json",
        "template_id",
    }
    _apply_changes(record, changes, allowed)
    session.flush()
    return record


def get_resume_record(session: Session, record_id: int) -> ResumeRecord | None:
    return session.get(ResumeRecord, record_id)


def get_resume_record_for_user(
    session: Session, record_id: int, phone: str
) -> ResumeRecord | None:
    """Return one resume only when it belongs to the authenticated user."""

    statement = select(ResumeRecord).where(
        ResumeRecord.id == record_id,
        ResumeRecord.phone == phone,
    )
    return session.scalar(statement)


def get_recent_resume_records(
    session: Session, phone: str, limit: int = 5
) -> list[ResumeRecord]:
    statement = (
        select(ResumeRecord)
        .where(ResumeRecord.phone == phone)
        .order_by(ResumeRecord.created_at.desc(), ResumeRecord.id.desc())
        .limit(limit)
    )
    return list(session.scalars(statement))


def delete_resume_record(session: Session, record_id: int) -> None:
    session.delete(_require(session, ResumeRecord, record_id))


def create_interview_session(
    session: Session,
    *,
    phone: str,
    position: str,
    job_description: str = "",
    resume_text: str = "",
    mode: str = "standard_live",
    feedback_mode: str = "live",
    policy_version: str = "interview-standard-v1",
    question_plan: list[dict[str, Any]] | None = None,
) -> InterviewSession:
    get_or_create_user(session, phone)
    interview = InterviewSession(
        phone=phone,
        position=position,
        job_description=job_description,
        resume_text=resume_text,
        mode=mode,
        feedback_mode=feedback_mode,
        policy_version=policy_version,
        question_plan_json=json.dumps(question_plan or [], ensure_ascii=False),
    )
    session.add(interview)
    session.flush()
    return interview


def update_interview_session(
    session: Session, interview_id: int, **changes: Any
) -> InterviewSession:
    interview = _require(session, InterviewSession, interview_id)
    if "conversation" in changes:
        changes["conversation_json"] = json.dumps(
            changes.pop("conversation"), ensure_ascii=False
        )
    if "question_plan" in changes:
        changes["question_plan_json"] = json.dumps(
            changes.pop("question_plan"), ensure_ascii=False
        )
    allowed = {
        "position",
        "job_description",
        "resume_text",
        "status",
        "current_question",
        "question_rounds",
        "follow_up_count",
        "conversation_json",
        "report",
        "mode",
        "feedback_mode",
        "policy_version",
        "question_plan_json",
    }
    _apply_changes(interview, changes, allowed)
    session.flush()
    return interview


def get_interview_session(
    session: Session, interview_id: int
) -> InterviewSession | None:
    return session.get(InterviewSession, interview_id)


def get_interview_session_for_user(
    session: Session, interview_id: int, phone: str
) -> InterviewSession | None:
    """Return one interview only when it belongs to the authenticated user."""

    statement = select(InterviewSession).where(
        InterviewSession.id == interview_id,
        InterviewSession.phone == phone,
    )
    return session.scalar(statement)


def get_recent_interview_sessions(
    session: Session, phone: str, limit: int = 5
) -> list[InterviewSession]:
    statement = (
        select(InterviewSession)
        .where(InterviewSession.phone == phone)
        .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
        .limit(limit)
    )
    return list(session.scalars(statement))


def delete_interview_session(session: Session, interview_id: int) -> None:
    session.delete(_require(session, InterviewSession, interview_id))


def _require(session: Session, entity_type: type[Any], identity: Any) -> Any:
    entity = session.get(entity_type, identity)
    if entity is None:
        raise LookupError(f"{entity_type.__name__} {identity!r} was not found")
    return entity


def _apply_changes(entity: Any, changes: dict[str, Any], allowed: set[str]) -> None:
    unexpected = set(changes) - allowed
    if unexpected:
        raise ValueError(f"Unsupported fields: {', '.join(sorted(unexpected))}")
    for field, value in changes.items():
        setattr(entity, field, value)
