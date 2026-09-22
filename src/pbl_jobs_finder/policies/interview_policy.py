"""Versioned interview modes and deterministic question-plan contracts."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

INTERVIEW_STANDARD_POLICY_VERSION = "interview-standard-v1"
DEFAULT_INTERVIEW_MODE = "standard_live"
DEFAULT_FEEDBACK_MODE = "live"
DEFAULT_DIFFICULTY = "standard"
DIFFICULTY_CHOICES = (("入门", "beginner"), ("标准", "standard"), ("挑战", "challenge"))
DIFFICULTY_FOLLOW_UP_LIMITS = {"beginner": 0, "standard": 1, "challenge": 2}
DIFFICULTY_GUIDANCE = {
    "beginner": "入门：围绕基础概念和一个实际步骤提问，提供清晰场景，不要求复杂取舍或精确参数，不追问。",
    "standard": "标准：考察核心思路、具体行动和验证方法；核心已覆盖即可换题，最多追问一次关键缺口。",
    "challenge": "挑战：考察复杂约束、方案取舍、边界条件和验证依据；最多追问两次，不强求未记录的实验数值。",
}
FEEDBACK_MODE_CHOICES = (
    ("即时反馈", "live"),
    ("面试后反馈", "deferred"),
)
_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "interview_policies.yaml"
)

PolicyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=300),
]


class InterviewPolicyError(ValueError):
    """The configured interview policy or requested mode is invalid."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InterviewCompetency(_StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    objective: PolicyText


class InterviewRoleProfile(_StrictModel):
    position_patterns: list[str] = Field(min_length=1)
    jd_patterns: list[str] = Field(min_length=1)
    bank_positions: list[str] = Field(min_length=1)
    focus_pattern: str
    objectives: dict[str, PolicyText]
    fallback_questions: dict[str, PolicyText]

    def matches(self, position: str, job_description: str) -> bool:
        return any(re.search(pattern, position, re.IGNORECASE) for pattern in self.position_patterns) or any(
            re.search(pattern, job_description, re.IGNORECASE) for pattern in self.jd_patterns
        )

    def accepts_question(self, question: str) -> bool:
        return re.search(self.focus_pattern, question, re.IGNORECASE) is not None


class InterviewMode(_StrictModel):
    label: PolicyText
    question_count: int = Field(strict=True, ge=2, le=8)
    max_follow_up_count: int = Field(strict=True, ge=0, le=3)
    competencies: list[InterviewCompetency] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def require_one_competency_per_question(self) -> InterviewMode:
        if len(self.competencies) != self.question_count:
            raise ValueError("interview competencies must match question_count")
        keys = [item.key for item in self.competencies]
        if len(set(keys)) != len(keys):
            raise ValueError("interview competency keys must be unique")
        return self

    def build_question_plan(self) -> list[dict[str, object]]:
        """Build the immutable, fact-neutral plan persisted with one session."""

        return [
            {
                "round": index,
                "competency": competency.key,
                "objective": competency.objective,
            }
            for index, competency in enumerate(self.competencies, 1)
        ]


class InterviewPolicy(_StrictModel):
    version: str
    label: PolicyText
    modes: dict[str, InterviewMode]
    role_profiles: dict[str, InterviewRoleProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_default_mode(self) -> InterviewPolicy:
        if DEFAULT_INTERVIEW_MODE not in self.modes:
            raise ValueError("interview policy must include the default mode")
        keys = {item.key for mode in self.modes.values() for item in mode.competencies}
        for profile in self.role_profiles.values():
            if not keys <= profile.objectives.keys() or not keys <= profile.fallback_questions.keys():
                raise ValueError("role profile must cover every interview competency")
            for pattern in [*profile.position_patterns, *profile.jd_patterns, profile.focus_pattern]:
                re.compile(pattern)
        return self

    def role_profile(self, position: str, job_description: str) -> InterviewRoleProfile | None:
        return next((profile for profile in self.role_profiles.values()
                     if profile.matches(position, job_description)), None)

    def build_question_plan(self, mode: str, position: str, job_description: str) -> list[dict[str, object]]:
        plan = self.get_mode(mode).build_question_plan()
        profile = self.role_profile(position, job_description)
        if profile is not None:
            for item in plan:
                key = str(item["competency"])
                item["objective"] = profile.objectives[key]
                item["fallback_question"] = profile.fallback_questions[key]
                item["focus_pattern"] = profile.focus_pattern
        return plan

    def get_mode(self, mode: str) -> InterviewMode:
        normalized = (mode or DEFAULT_INTERVIEW_MODE).strip()
        selected = self.modes.get(normalized)
        if selected is None:
            raise InterviewPolicyError(f"未知的面试模式：{normalized}")
        return selected


class _InterviewPolicyRegistry(_StrictModel):
    policies: dict[str, InterviewPolicy]

    @model_validator(mode="after")
    def require_matching_versions(self) -> _InterviewPolicyRegistry:
        for key, policy in self.policies.items():
            if key != policy.version:
                raise ValueError("interview policy key must match its version")
        return self


@lru_cache(maxsize=1)
def _load_policy_registry() -> _InterviewPolicyRegistry:
    try:
        raw = yaml.safe_load(_POLICY_PATH.read_text(encoding="utf-8"))
        return _InterviewPolicyRegistry.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError, re.error) as exc:
        raise InterviewPolicyError("模拟面试策略配置无效") from exc


def get_interview_policy(
    version: str = INTERVIEW_STANDARD_POLICY_VERSION,
) -> InterviewPolicy:
    """Return a reviewed local interview policy by immutable version."""

    policy = _load_policy_registry().policies.get(version)
    if policy is None:
        raise InterviewPolicyError(f"未知的模拟面试策略版本：{version}")
    return policy


def get_interview_mode_choices(
    version: str = INTERVIEW_STANDARD_POLICY_VERSION,
) -> tuple[tuple[str, str], ...]:
    policy = get_interview_policy(version)
    return tuple((mode.label, mode_id) for mode_id, mode in policy.modes.items())


def normalize_feedback_mode(value: str) -> str:
    normalized = (value or DEFAULT_FEEDBACK_MODE).strip()
    allowed = {mode_id for _, mode_id in FEEDBACK_MODE_CHOICES}
    if normalized not in allowed:
        raise InterviewPolicyError(f"未知的反馈方式：{normalized}")
    return normalized


def normalize_difficulty(value: str) -> str:
    normalized = (value or DEFAULT_DIFFICULTY).strip()
    if normalized not in DIFFICULTY_FOLLOW_UP_LIMITS:
        raise InterviewPolicyError(f"未知的面试难度：{normalized}")
    return normalized


__all__ = [
    "DEFAULT_DIFFICULTY",
    "DEFAULT_FEEDBACK_MODE",
    "DEFAULT_INTERVIEW_MODE",
    "DIFFICULTY_CHOICES",
    "DIFFICULTY_FOLLOW_UP_LIMITS",
    "DIFFICULTY_GUIDANCE",
    "FEEDBACK_MODE_CHOICES",
    "INTERVIEW_STANDARD_POLICY_VERSION",
    "InterviewCompetency",
    "InterviewMode",
    "InterviewPolicy",
    "InterviewPolicyError",
    "get_interview_mode_choices",
    "get_interview_policy",
    "normalize_difficulty",
    "normalize_feedback_mode",
]
