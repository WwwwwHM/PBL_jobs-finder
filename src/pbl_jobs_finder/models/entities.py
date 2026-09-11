"""SQLAlchemy entities for users, resume diagnoses, and interviews."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Declarative base shared by all application entities."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("length(phone) = 11", name="ck_users_phone_length"),)

    phone: Mapped[str] = mapped_column(String(11), primary_key=True)
    nickname: Mapped[str] = mapped_column(String(50), default="求职者", nullable=False)
    total_usage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    resume_records: Mapped[list[ResumeRecord]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    interview_sessions: Mapped[list[InterviewSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ResumeRecord(TimestampMixin, Base):
    __tablename__ = "resume_records"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_resume_score_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(
        ForeignKey("users.phone", ondelete="CASCADE"), index=True, nullable=False
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_position: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_keywords_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    suggestions: Mapped[str] = mapped_column(Text, nullable=False)
    optimized_text: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship(back_populates="resume_records")


class InterviewSession(TimestampMixin, Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        CheckConstraint("question_rounds >= 0", name="ck_interview_rounds_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(
        ForeignKey("users.phone", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[str] = mapped_column(String(100), nullable=False)
    job_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resume_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    current_question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    question_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversation_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    report: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="interview_sessions")
