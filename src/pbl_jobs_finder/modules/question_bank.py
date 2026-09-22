"""Schema and loading helpers for the interview question bank."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
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
    reference_answer: str = Field(default="", max_length=20000)
    source: str = Field(default="", max_length=1000)
    parent_question: str = Field(default="", max_length=1000)

    def metadata(self) -> dict[str, str | bool]:
        return {
            "position": self.position,
            "difficulty": self.difficulty,
            "category": self.category,
            "reference_answer": self.reference_answer,
            "has_reference_answer": bool(self.reference_answer),
            "source": self.source,
            "parent_question": self.parent_question,
        }

    def embedding_text(self) -> str:
        text = (
            f"岗位：{self.position}\n"
            f"分类：{self.category}\n"
            f"难度：{self.difficulty}\n"
            f"问题：{self.question}"
        )
        if self.parent_question:
            text += f"\n所属主问题：{self.parent_question}"
        return text


_QUESTION_LIST_ADAPTER = TypeAdapter(list[InterviewQuestion])


def load_question_bank(
    path: str | Path = DEFAULT_QUESTION_BANK_PATH,
    *,
    position: str = "AI大模型应用开发工程师",
    difficulty: Literal["basic", "medium", "advanced"] = "medium",
    category: str = "AI应用开发",
) -> list[InterviewQuestion]:
    """Load a JSON bank or Markdown questions with their reference answers."""

    source = Path(path)
    try:
        content = source.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(f"面试题库不存在：{source}") from exc
    if source.suffix.lower() in {".md", ".markdown"}:
        questions = _parse_markdown(
            content, source=source, position=position,
            difficulty=difficulty, category=category,
        )
    else:
        try:
            raw_data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"面试题库不是合法 JSON：{source}") from exc
        questions = _QUESTION_LIST_ADAPTER.validate_python(raw_data)
    if not questions:
        raise ValueError("面试题库不能为空")
    ids = [question.id for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("面试题库包含重复 ID")
    return questions


def _parse_markdown(
    content: str,
    *,
    source: Path,
    position: str,
    difficulty: str,
    category: str,
) -> list[InterviewQuestion]:
    # Token source maps preserve answer formatting and ignore headings in code fences.
    tokens = MarkdownIt("commonmark").parse(content)
    headings = [
        (token, tokens[index + 1].content)
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.level == 0
        and token.tag in {"h1", "h2", "h3"}
    ]
    lines = content.splitlines(keepends=True)
    questions = []
    parent_question = ""
    for index, (token, title) in enumerate(headings):
        if token.tag == "h1":
            parent_question = ""
            continue
        question = re.sub(r"^\d+(?:\.\d+)*(?:[.、．]\s*|\s+)", "", title).strip()
        question = re.sub(r"^追问\s*[:：]\s*", "", question).strip()
        if token.tag == "h3" and not parent_question:
            raise ValueError(f"追问缺少所属的主问题：{title}")
        parent = parent_question if token.tag == "h3" else ""
        if token.tag == "h2":
            parent_question = question
        end = headings[index + 1][0].map[0] if index + 1 < len(headings) else len(lines)
        answer = "".join(lines[token.map[1]:end]).strip()
        # Identity excludes numbering and answer text so edits to answers update in place.
        identity = json.dumps([source.name, parent, question], ensure_ascii=False)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        questions.append(InterviewQuestion(
            id=f"q_md_{digest}", question=question, position=position,
            difficulty=difficulty, category=category, reference_answer=answer,
            source=source.name, parent_question=parent,
        ))
    return questions


__all__ = ["DEFAULT_QUESTION_BANK_PATH", "InterviewQuestion", "load_question_bank"]
