"""Schema and loading helpers for the interview question bank."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

DEFAULT_QUESTION_BANK_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "interview_questions.json"
)


class InterviewQuestion(BaseModel):
    """One validated interview question and its retrieval metadata."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^q_[a-z0-9_]+$", min_length=4, max_length=80)
    question: str = Field(min_length=8, max_length=1000)
    position: str = Field(min_length=2, max_length=100)
    difficulty: Literal["basic", "medium", "advanced"]
    category: str = Field(min_length=2, max_length=100)

    def metadata(self) -> dict[str, str]:
        return {
            "position": self.position,
            "difficulty": self.difficulty,
            "category": self.category,
        }

    def embedding_text(self) -> str:
        return (
            f"岗位：{self.position}\n"
            f"分类：{self.category}\n"
            f"难度：{self.difficulty}\n"
            f"问题：{self.question}"
        )


_QUESTION_LIST_ADAPTER = TypeAdapter(list[InterviewQuestion])


def load_question_bank(path: str | Path = DEFAULT_QUESTION_BANK_PATH) -> list[InterviewQuestion]:
    """Load and strictly validate a UTF-8 JSON question bank."""

    source = Path(path)
    try:
        raw_data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"面试题库不存在：{source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"面试题库不是合法 JSON：{source}") from exc
    questions = _QUESTION_LIST_ADAPTER.validate_python(raw_data)
    if not questions:
        raise ValueError("面试题库不能为空")
    ids = [question.id for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("面试题库包含重复 ID")
    return questions


__all__ = ["DEFAULT_QUESTION_BANK_PATH", "InterviewQuestion", "load_question_bank"]
