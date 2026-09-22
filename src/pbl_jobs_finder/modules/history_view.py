"""Safe Markdown presenters for authenticated history details."""

from __future__ import annotations

import html

from pbl_jobs_finder.modules.history import (
    InterviewHistoryDetail,
    ResumeHistoryDetail,
)
from pbl_jobs_finder.modules.resume_templates import get_resume_template
from pbl_jobs_finder.policies.interview_policy import (
    InterviewPolicyError,
    get_interview_policy,
)
from pbl_jobs_finder.policies.resume_policy import ResumePolicyError, get_resume_policy

HISTORY_STATUS_LABELS = {
    "created": "待开始",
    "in_progress": "进行中",
    "completed": "已完成",
}
RESUME_DIMENSION_LABELS = {
    "hard_skill_match": "硬技能匹配度",
    "experience_relevance": "工作经验相关度",
    "soft_skill_match": "软性能力匹配度",
    "education_match": "教育背景匹配度",
    "keyword_coverage": "关键词覆盖率",
    "resume_quality": "简历质量",
}


def _text(value: object) -> str:
    return html.escape(str(value or "").strip()).replace("|", "\\|")


def _items(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value if item is not None and str(item).strip()]


def _bullets(value: object, empty: str = "暂无") -> str:
    items = _items(value)
    return "\n".join(f"- {item}" for item in items) if items else empty


def _resume_policy_label(detail: ResumeHistoryDetail) -> str:
    try:
        return get_resume_policy(detail.policy_version).label
    except ResumePolicyError:
        return detail.policy_version


def _template_label(template_id: str) -> str:
    try:
        return get_resume_template(template_id).label
    except ValueError:
        return template_id


def format_resume_history_detail(detail: ResumeHistoryDetail) -> str:
    """Render a resume diagnosis detail without trusting stored text as HTML."""

    metadata = detail.diagnosis
    dimensions = metadata.get("dimensions")
    dimension_sections: list[str] = []
    if isinstance(dimensions, dict):
        for key, label in RESUME_DIMENSION_LABELS.items():
            result = dimensions.get(key)
            if not isinstance(result, dict):
                continue
            score = result.get("score")
            confidence = _text(result.get("confidence", ""))
            score_text = f"{score} / 100" if isinstance(score, int) else "未记录"
            confidence_text = f" · 置信度 {confidence}" if confidence else ""
            dimension_sections.append(
                f"#### {_text(label)}\n\n"
                f"**得分：** {score_text}{confidence_text}\n\n"
                f"**证据**\n\n{_bullets(result.get('evidence'))}\n\n"
                f"**差距**\n\n{_bullets(result.get('gaps'))}\n\n"
                f"**建议**\n\n{_bullets(result.get('recommendations'))}"
            )
    dimension_markdown = (
        "\n\n".join(dimension_sections)
        if dimension_sections
        else "该记录未保存六维诊断明细。"
    )
    optimized = html.escape(detail.optimized_text or "暂无优化稿")
    grade = f" · 等级 {_text(detail.grade)}" if detail.grade else ""
    return f"""## 简历诊断详情

**记录编号：** {detail.record_id}<br>
**诊断时间：** {_text(detail.created_at)}<br>
**目标岗位：** {_text(detail.target_position)}<br>
**综合评分：** {detail.score} / 100{grade}<br>
**诊断策略：** {_text(_resume_policy_label(detail))}<br>
**简历模板：** {_text(_template_label(detail.template_id))}

### 优势

{_bullets(metadata.get("strengths"))}

### 缺失关键词

{_bullets(detail.missing_keywords)}

### 六维诊断

{dimension_markdown}

### 综合建议与 STAR 示例

{_text(detail.suggestions)}

### 优化稿

<pre>{optimized}</pre>"""


def _interview_mode_label(detail: InterviewHistoryDetail) -> str:
    try:
        return get_interview_policy(detail.policy_version).get_mode(detail.mode).label
    except InterviewPolicyError:
        return detail.mode


def _score(value: object) -> int | str:
    return value if isinstance(value, int) and 0 <= value <= 100 else "-"


def format_interview_history_detail(detail: InterviewHistoryDetail) -> str:
    """Render an interview transcript and report from persisted data."""

    feedback_labels = {"live": "即时反馈", "deferred": "面试后反馈"}
    difficulty_labels = {"beginner": "入门", "standard": "标准", "challenge": "挑战"}
    transcript: list[str] = []
    kind_labels = {
        "main_question": "面试官",
        "follow_up": "面试官追问",
        "answer": "你的回答",
        "skipped": "已跳过",
        "feedback": "AI 反馈",
    }
    for item in detail.conversation:
        content = _text(item.get("content", ""))
        if not content:
            continue
        kind = str(item.get("kind", ""))
        role = kind_labels.get(
            kind, "面试官" if item.get("role") == "interviewer" else "你的回答"
        )
        round_value = item.get("round")
        round_text = f" · 第 {round_value} 题" if isinstance(round_value, int) else ""
        transcript.append(f"**{_text(role)}{round_text}**\n\n{content}")
    transcript_markdown = "\n\n---\n\n".join(transcript) or "暂无问答记录。"

    report = detail.report
    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    logic_score = _score(scores.get("logic"))
    professional_score = _score(scores.get("professional"))
    communication_score = _score(scores.get("communication"))
    report_markdown = "面试尚未生成最终报告。"
    if report:
        references: list[str] = []
        raw_references = report.get("reference_answers")
        if isinstance(raw_references, list):
            for index, item in enumerate(raw_references, 1):
                if not isinstance(item, dict):
                    continue
                references.append(
                    f"**问题 {index}：** {_text(item.get('question', ''))}\n\n"
                    f"**参考回答：** {_text(item.get('answer', ''))}"
                )
        references_markdown = "\n\n".join(references) if references else "暂无"
        report_markdown = f"""| 逻辑能力 | 专业能力 | 表达能力 |
| ---: | ---: | ---: |
| {logic_score} / 100 | {professional_score} / 100 | {communication_score} / 100 |

**总体评价**

{_text(report.get("summary", "暂无"))}

**知识盲区**

{_bullets(report.get("knowledge_gaps"))}

**改进建议**

{_bullets(report.get("improvement_suggestions"))}

**参考回答**

{references_markdown}"""

    return f"""## 模拟面试详情

**记录编号：** {detail.session_id}<br>
**面试时间：** {_text(detail.created_at)}<br>
**目标岗位：** {_text(detail.position)}<br>
**状态：** {HISTORY_STATUS_LABELS.get(detail.status, "状态异常")}<br>
**面试节奏：** {_text(_interview_mode_label(detail))}<br>
**面试难度：** {difficulty_labels.get(detail.difficulty, _text(detail.difficulty))}<br>
**反馈方式：** {feedback_labels.get(detail.feedback_mode, _text(detail.feedback_mode))}<br>
**策略版本：** {_text(detail.policy_version)}

### 问答回放

{transcript_markdown}

### 面试报告

{report_markdown}"""


__all__ = [
    "HISTORY_STATUS_LABELS",
    "format_interview_history_detail",
    "format_resume_history_detail",
]
