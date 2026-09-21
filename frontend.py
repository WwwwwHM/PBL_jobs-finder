"""Gradio frontend with token-backed login persistence and shared quota."""

from __future__ import annotations

import logging

import gradio as gr

from pbl_jobs_finder.exceptions import (
    AuthenticationError,
    EmbeddingConfigurationError,
    EmbeddingServiceError,
    InterviewAccessError,
    InterviewUnavailableError,
    InterviewValidationError,
    LLMConfigurationError,
    LLMServiceError,
    QuotaExceededError,
    ResumeAccessError,
    ResumeParseError,
    ResumePDFError,
    ResumeResponseError,
    ResumeValidationError,
)
from pbl_jobs_finder.modules.auth import (
    revoke_token,
    send_verification_code,
    verify_login,
    verify_token,
)
from pbl_jobs_finder.modules.history import history_service
from pbl_jobs_finder.modules.history_view import (
    HISTORY_STATUS_LABELS,
    format_interview_history_detail,
    format_resume_history_detail,
)
from pbl_jobs_finder.modules.interview_agent import InterviewReport, InterviewService
from pbl_jobs_finder.modules.quota import quota_service
from pbl_jobs_finder.modules.resume_diagnosis import ResumeDiagnosisService
from pbl_jobs_finder.modules.resume_pdf import document_to_markdown
from pbl_jobs_finder.modules.resume_templates import (
    DEFAULT_RESUME_TEMPLATE_ID,
    RESUME_TEMPLATE_CHOICES,
    get_resume_template,
)
from pbl_jobs_finder.policies.interview_policy import (
    DEFAULT_FEEDBACK_MODE,
    DEFAULT_INTERVIEW_MODE,
    FEEDBACK_MODE_CHOICES,
    INTERVIEW_STANDARD_POLICY_VERSION,
    get_interview_mode_choices,
)
from pbl_jobs_finder.utils.logging import configure_logging, report_exception

logger = logging.getLogger(__name__)

STORAGE_KEY = "pbl_jobs_finder.auth_token"
READ_TOKEN_JS = f"""() => {{
    try {{ return localStorage.getItem('{STORAGE_KEY}') || ''; }}
    catch {{ return ''; }}
}}"""
WRITE_TOKEN_JS = f"""(token) => {{
    try {{
        if (token) localStorage.setItem('{STORAGE_KEY}', token);
        else localStorage.removeItem('{STORAGE_KEY}');
    }} catch {{ /* Session-only login when storage is unavailable. */ }}
    return token;
}}"""
resume_service = ResumeDiagnosisService()
interview_service = InterviewService()

RESUME_HISTORY_HEADERS = ["编号", "时间", "目标岗位", "匹配度"]
INTERVIEW_HISTORY_HEADERS = ["编号", "时间", "目标岗位", "问答轮数", "状态"]


def request_code(phone: str) -> str:
    """Validate the phone field and return a frontend status message.

    Code generation and console output are handled by the authentication
    service; this callback only translates its result into UI text.
    """

    try:
        phone = (phone or "").strip()
        result = send_verification_code(phone)
        return result.message
    except Exception as exc:  # noqa: BLE001 - callback must return a stable UI response
        error_id = report_exception(logger, "auth.request_code", exc)
        return f"验证码发送失败，请稍后重试（错误编号：{error_id}）"


def login(phone: str, code: str) -> tuple:
    """Authenticate and provide the token to server state and the browser."""

    try:
        result = verify_login((phone or "").strip(), (code or "").strip())
    except Exception as exc:  # noqa: BLE001 - callback must return a stable UI response
        error_id = report_exception(logger, "auth.login", exc)
        return _logged_out(f"登录失败，请稍后重试（错误编号：{error_id}）")
    if not result.success or result.data is None:
        return _logged_out(result.message)
    return restore_login(result.data)


def _logged_out(message: str = "") -> tuple:
    return (
        gr.update(visible=True),
        gr.update(visible=False),
        message,
        "",
        "",
        "",
        "",
    )


def restore_login(token: str) -> tuple:
    """Never trust a browser token or identity without backend validation."""

    try:
        user = verify_token(token)
        if user is None:
            return _logged_out("登录已失效，请重新登录" if token else "")
        status = quota_service.status(token)
    except AuthenticationError:
        return _logged_out("登录已失效，请重新登录")
    except Exception as exc:  # noqa: BLE001 - callback must return a stable UI response
        error_id = report_exception(logger, "auth.restore_login", exc)
        return _logged_out(f"登录状态恢复失败（错误编号：{error_id}）")
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        "",
        f"{user['nickname']} · {user['phone']}",
        f"今日剩余 {status.remaining} / {status.limit} 次",
        token,
        token,
    )


def logout(token: str) -> tuple:
    """Revoke backend credentials and clear both browser and session state."""

    try:
        revoke_token(token)
        message = "已退出登录"
    except Exception as exc:  # noqa: BLE001 - local state is still cleared
        error_id = report_exception(logger, "auth.logout", exc)
        message = f"本地登录状态已清除（错误编号：{error_id}）"
    return (*_logged_out(message), "", "")


def load_history_callback(
    token: str,
) -> tuple[list[list[object]], list[list[object]], str, str]:
    """Load recent record summaries without exposing stored source content."""

    if not (token or "").strip():
        return [], [], "", ""
    try:
        snapshot = history_service.get_recent(token)
    except AuthenticationError as exc:
        logger.warning("History rejected exception=%s", type(exc).__name__)
        return [], [], f"**记录加载失败：** {exc}", ""
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures debuggable
        error_id = report_exception(logger, "history.load", exc)
        return (
            [],
            [],
            f"**记录加载失败：** 请稍后重试（错误编号：{error_id}）",
            "",
        )

    resume_rows = [
        [item.record_id, item.created_at, item.target_position, f"{item.score} / 100"]
        for item in snapshot.resumes
    ]
    interview_rows = [
        [
            item.session_id,
            item.created_at,
            item.position,
            f"{item.question_rounds} 轮",
            HISTORY_STATUS_LABELS.get(item.status, "状态异常"),
        ]
        for item in snapshot.interviews
    ]
    if not resume_rows and not interview_rows:
        message = "暂无历史记录。完成一次简历诊断或开始一轮模拟面试后会显示在这里。"
    else:
        message = (
            f"已加载最近记录：简历诊断 {len(resume_rows)} 条，"
            f"模拟面试 {len(interview_rows)} 条。"
        )
    return resume_rows, interview_rows, message, ""


def clear_history_workspace() -> tuple[list[object], list[object], str, str]:
    """Clear history rows when authentication is removed."""

    return [], [], "", ""


def load_resume_history_detail_callback(token: str, evt: gr.SelectData) -> str:
    """Load one selected resume diagnosis after ownership verification."""

    if not evt.selected:
        return ""
    try:
        detail = history_service.get_resume_detail(
            token, _selected_history_id(evt, ResumeAccessError)
        )
    except (AuthenticationError, ResumeAccessError) as exc:
        logger.warning("Resume history detail rejected exception=%s", type(exc).__name__)
        return f"**详情加载失败：** {exc}"
    except Exception as exc:  # noqa: BLE001 - return a searchable error reference
        error_id = report_exception(logger, "history.resume_detail", exc)
        return f"**详情加载失败：** 请稍后重试（错误编号：{error_id}）"
    return format_resume_history_detail(detail)


def load_interview_history_detail_callback(token: str, evt: gr.SelectData) -> str:
    """Load one selected interview transcript after ownership verification."""

    if not evt.selected:
        return ""
    try:
        detail = history_service.get_interview_detail(
            token, _selected_history_id(evt, InterviewAccessError)
        )
    except (AuthenticationError, InterviewAccessError) as exc:
        logger.warning(
            "Interview history detail rejected exception=%s", type(exc).__name__
        )
        return f"**详情加载失败：** {exc}"
    except Exception as exc:  # noqa: BLE001 - return a searchable error reference
        error_id = report_exception(logger, "history.interview_detail", exc)
        return f"**详情加载失败：** 请稍后重试（错误编号：{error_id}）"
    return format_interview_history_detail(detail)


def _selected_history_id(
    evt: gr.SelectData,
    error_type: type[InterviewAccessError | ResumeAccessError],
) -> int:
    row = evt.row_value
    try:
        record_id = int(row[0]) if isinstance(row, list) else 0
    except (TypeError, ValueError):
        record_id = 0
    if record_id <= 0:
        raise error_type("历史记录选择已失效，请刷新后重试")
    return record_id


def diagnose_resume_callback(
    uploaded_file: str | None,
    pasted_text: str,
    position: str,
    token: str,
) -> tuple:
    """Run one diagnosis and expose the editable optimized draft."""

    try:
        outcome = resume_service.diagnose(
            token=token,
            position=position,
            uploaded_file=uploaded_file,
            pasted_text=pasted_text,
        )
        remaining = quota_service.status(token).remaining
    except (
        AuthenticationError,
        LLMConfigurationError,
        LLMServiceError,
        QuotaExceededError,
        ResumeParseError,
        ResumeResponseError,
        ResumeValidationError,
    ) as exc:
        reference = _callback_error_reference(
            "resume.diagnose",
            exc,
            has_upload=bool(uploaded_file),
            pasted_text_chars=len(pasted_text or ""),
            position_chars=len(position or ""),
        )
        return (
            f"**诊断未完成：** {exc}{reference}",
            "",
            "",
            "",
            "",
            None,
            _quota_label(token),
            gr.update(visible=False),
            "",
            gr.update(value=None),
            gr.update(visible=False),
            gr.update(value=None, visible=False),
            "",
        )
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures debuggable
        error_id = report_exception(
            logger,
            "resume.diagnose",
            exc,
            has_upload=bool(uploaded_file),
            pasted_text_chars=len(pasted_text or ""),
            position_chars=len(position or ""),
        )
        return _diagnosis_failure(
            f"系统异常，请稍后重试（错误编号：{error_id}）",
            token,
        )

    diagnosis = outcome.diagnosis
    keywords = "、".join(diagnosis.missing_keywords) or "未发现明显缺失关键词"
    suggestions = (
        f"{diagnosis.suggestions}\n\n### STAR 改写示例\n{diagnosis.star_examples}"
    )
    return (
        "诊断已完成。请检查当前优化稿，再生成新版 PDF 简历。",
        f"## {diagnosis.score} / 100",
        keywords,
        suggestions,
        diagnosis.optimized_text,
        outcome.record_id,
        f"今日剩余 {remaining} / {quota_service.limit} 次",
        gr.update(visible=True),
        "",
        gr.update(value=None),
        gr.update(visible=False),
        gr.update(value=None, visible=False),
        "",
    )


def open_supplement_callback(record_id: int | str | None) -> tuple:
    """Open the supplemental-experience panel for a completed diagnosis."""

    if not record_id:
        return (
            gr.update(visible=False),
            "",
            gr.update(value=None, visible=False),
            "**请先完成一次简历诊断。**",
        )
    return (
        gr.update(visible=True),
        "",
        gr.update(value=None, visible=False),
        "",
    )


def cancel_supplement_callback() -> tuple:
    """Close and clear the supplemental panel without changing the draft."""

    return gr.update(visible=False), "", ""


def begin_resume_generation() -> tuple:
    """Disable panel actions before the queued model and PDF work begins."""

    disabled = gr.update(interactive=False)
    return (
        disabled,
        disabled,
        "正在整理补充信息并生成 PDF，请稍候...",
        gr.update(value=None, visible=False),
    )


def generate_resume_callback(
    token: str,
    record_id: int | str | None,
    optimized_text: str,
    supplemental_experience: str,
    photo_file: str | None = None,
    template_id: str = DEFAULT_RESUME_TEMPLATE_ID,
) -> tuple:
    """Generate a structured resume and downloadable PDF."""

    try:
        outcome = resume_service.generate_pdf_resume(
            token=token,
            record_id=record_id,
            optimized_text=optimized_text,
            supplemental_experience=supplemental_experience,
            photo_file=photo_file,
            template_id=template_id,
        )
    except (
        AuthenticationError,
        LLMConfigurationError,
        LLMServiceError,
        ResumeAccessError,
        ResumePDFError,
        ResumeResponseError,
        ResumeValidationError,
        ValueError,
    ) as exc:
        reference = _callback_error_reference(
            "resume.generate_pdf",
            exc,
            optimized_text_chars=len(optimized_text or ""),
            record_id=record_id if isinstance(record_id, int) else "invalid",
            supplemental_chars=len(supplemental_experience or ""),
            has_photo=bool(photo_file),
            template_id=template_id,
        )
        enabled = gr.update(interactive=True)
        return (
            gr.update(visible=True),
            gr.update(),
            gr.update(value=None, visible=False),
            f"**生成失败：** {exc}{reference}",
            gr.update(),
            enabled,
            enabled,
        )
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures debuggable
        error_id = report_exception(
            logger,
            "resume.generate_pdf",
            exc,
            optimized_text_chars=len(optimized_text or ""),
            record_id=record_id if isinstance(record_id, int) else "invalid",
            supplemental_chars=len(supplemental_experience or ""),
            has_photo=bool(photo_file),
            template_id=template_id,
        )
        return _generation_failure(f"系统异常，请稍后重试（错误编号：{error_id}）")
    enabled = gr.update(interactive=True)
    template_label = get_resume_template(outcome.template_id).label
    return (
        gr.update(visible=False),
        "",
        gr.update(value=str(outcome.pdf_path), visible=True),
        f"{template_label} PDF 简历已生成，可直接下载。",
        document_to_markdown(outcome.document),
        enabled,
        enabled,
    )


def begin_interview_start() -> tuple:
    """Disable duplicate starts while retrieval and generation are running."""

    return gr.update(interactive=False), "正在检索题库并生成第一道问题..."


def use_optimized_resume_callback(
    optimized_text: str,
    diagnosed_position: str,
    interview_position: str,
) -> tuple:
    """Copy the current diagnosis or generated resume into interview context."""

    resume = (optimized_text or "").strip()
    if not resume:
        return (
            gr.update(),
            gr.update(),
            "**未导入简历：** 请先在简历诊断中生成优化稿。",
        )
    position = (interview_position or "").strip() or (
        diagnosed_position or ""
    ).strip()
    return resume, position, "已导入当前优化稿。"


def start_interview_callback(
    position: str,
    job_description: str,
    resume_text: str,
    token: str,
    mode: str = DEFAULT_INTERVIEW_MODE,
    feedback_mode: str = DEFAULT_FEEDBACK_MODE,
) -> tuple:
    """Create an interview session and display its first question."""

    try:
        outcome = interview_service.start(
            token=token,
            position=position,
            job_description=job_description,
            resume_text=resume_text,
            mode=mode,
            feedback_mode=feedback_mode,
        )
        remaining = quota_service.status(token).remaining
    except (
        AuthenticationError,
        EmbeddingConfigurationError,
        EmbeddingServiceError,
        InterviewUnavailableError,
        InterviewValidationError,
        LLMConfigurationError,
        LLMServiceError,
        QuotaExceededError,
        ValueError,
    ) as exc:
        reference = _callback_error_reference(
            "interview.start",
            exc,
            position_chars=len(position or ""),
            job_description_chars=len(job_description or ""),
            resume_text_chars=len(resume_text or ""),
        )
        return (
            f"**面试未开始：** {exc}{reference}",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            _quota_label(token),
            gr.update(interactive=True),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures debuggable
        error_id = report_exception(
            logger,
            "interview.start",
            exc,
            position_chars=len(position or ""),
            job_description_chars=len(job_description or ""),
            resume_text_chars=len(resume_text or ""),
        )
        return (
            f"**面试未开始：** 系统异常，请稍后重试（错误编号：{error_id}）",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            _quota_label(token),
            gr.update(interactive=True),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )

    return (
        "面试已开始。",
        outcome.session_id,
        f"第 {outcome.question_round} 题 / 共 {outcome.total_questions} 题",
        f"### 面试官提问\n\n{outcome.question}",
        gr.update(visible=True),
        f"今日剩余 {remaining} / {quota_service.limit} 次",
        gr.update(interactive=True),
        gr.update(value="", interactive=True),
        gr.update(interactive=True),
        "",
        outcome.question,
    )


def begin_answer_submission() -> tuple:
    """Prevent duplicates while AI evaluates the answer and advances state."""

    return gr.update(interactive=False), "正在评价回答并生成后续问题..."


def submit_interview_answer_callback(
    answer: str,
    session_id: int | str | None,
    token: str,
    expected_question: str,
) -> tuple:
    """Evaluate an answer and display feedback plus the following question."""

    try:
        outcome = interview_service.submit_answer(
            token=token,
            session_id=session_id,
            answer=answer,
            expected_question=expected_question,
        )
    except (
        AuthenticationError,
        InterviewAccessError,
        InterviewUnavailableError,
        InterviewValidationError,
    ) as exc:
        reference = _callback_error_reference(
            "interview.submit_answer",
            exc,
            answer_chars=len(answer or ""),
            session_id=session_id if isinstance(session_id, int) else "invalid",
        )
        return (
            f"**回答未保存：** {exc}{reference}",
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    except Exception as exc:  # noqa: BLE001 - keep unexpected failures debuggable
        error_id = report_exception(
            logger,
            "interview.submit_answer",
            exc,
            answer_chars=len(answer or ""),
            session_id=session_id if isinstance(session_id, int) else "invalid",
        )
        return (
            f"**回答未保存：** 系统异常，请稍后重试（错误编号：{error_id}）",
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    feedback = (
        f"### AI 反馈\n\n{outcome.feedback}"
        if outcome.feedback
        else "本轮反馈将在面试结束后统一展示。"
    )
    if outcome.is_finished:
        if outcome.report is None:
            error_id = report_exception(
                logger,
                "interview.submit_answer.missing_report",
                RuntimeError("completed interview returned no report"),
                session_id=outcome.session_id,
            )
            return (
                f"**报告展示失败：** 请点击“重新开始”开启新一轮面试（错误编号：{error_id}）",
                gr.update(value="", interactive=False),
                gr.update(interactive=False),
                f"已完成 {outcome.total_questions} 题",
                "### 本轮面试已完成",
                "",
                "面试已完成，但报告未能展示。",
            )
        return (
            _format_interview_report(outcome.report),
            gr.update(value="", interactive=False),
            gr.update(interactive=False),
            f"已完成 {outcome.total_questions} 题",
            "### 本轮面试已完成",
            "",
            "面试已完成，报告已保存。可点击“重新开始”开启新一轮面试。",
        )
    if outcome.is_follow_up:
        progress = (
            f"第 {outcome.question_round} 题 / 共 {outcome.total_questions} 题 · "
            f"追问 {outcome.follow_up_count} / {outcome.max_follow_up_count}"
        )
        question = f"### 面试官追问\n\n{outcome.next_question}"
        status = "请继续回答本题追问。"
    else:
        progress = (
            f"第 {outcome.question_round} 题 / 共 {outcome.total_questions} 题"
        )
        question = f"### 面试官提问\n\n{outcome.next_question}"
        status = f"已进入第 {outcome.question_round} 题。"
    return (
        feedback,
        gr.update(value="", interactive=True),
        gr.update(interactive=True),
        progress,
        question,
        outcome.next_question,
        status,
    )


def _format_interview_report(report: InterviewReport) -> str:
    """Render a validated interview report as concise Markdown."""

    def safe(value: str) -> str:
        return value.replace("|", "\\|")

    gaps = "\n".join(f"- {safe(item)}" for item in report.knowledge_gaps)
    suggestions = "\n".join(
        f"- {safe(item)}" for item in report.improvement_suggestions
    )
    references = "\n\n".join(
        f"**问题 {index}：** {safe(item.question)}\n\n"
        f"**参考回答：** {safe(item.answer)}"
        for index, item in enumerate(report.reference_answers, 1)
    )
    return f"""## 面试报告

| 逻辑能力 | 专业能力 | 表达能力 |
| ---: | ---: | ---: |
| {report.logic_score} / 100 | {report.professional_score} / 100 | {report.communication_score} / 100 |

### 总体评价

{safe(report.summary)}

### 知识盲区

{gaps}

### 改进建议

{suggestions}

### 参考回答

{references}"""


def _quota_label(token: str) -> str:
    try:
        status = quota_service.status(token)
    except AuthenticationError:
        return ""
    except Exception as exc:  # noqa: BLE001 - never hide the original callback error
        report_exception(logger, "quota.status", exc)
        return ""
    return f"今日剩余 {status.remaining} / {status.limit} 次"


def _callback_error_reference(
    operation: str,
    error: Exception,
    **context: object,
) -> str:
    if isinstance(
        error,
        (
            AuthenticationError,
            InterviewAccessError,
            InterviewValidationError,
            QuotaExceededError,
            ResumeValidationError,
        ),
    ):
        logger.warning(
            "Operation rejected operation=%s exception=%s",
            operation,
            type(error).__name__,
        )
        return ""
    error_id = report_exception(logger, operation, error, **context)
    return f"\n\n错误编号：`{error_id}`"


def _diagnosis_failure(message: str, token: str) -> tuple:
    return (
        f"**诊断未完成：** {message}",
        "",
        "",
        "",
        "",
        None,
        _quota_label(token),
        gr.update(visible=False),
        "",
        gr.update(value=None),
        gr.update(visible=False),
        gr.update(value=None, visible=False),
        "",
    )


def _generation_failure(message: str) -> tuple:
    enabled = gr.update(interactive=True)
    return (
        gr.update(visible=True),
        gr.update(),
        gr.update(value=None, visible=False),
        f"**生成失败：** {message}",
        gr.update(),
        enabled,
        enabled,
    )


def clear_resume_workspace() -> tuple:
    """Remove prior-user resume data from a reused browser session."""

    return (
        None,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        None,
        gr.update(visible=False),
        "",
        gr.update(value=None),
        gr.update(visible=False),
        gr.update(value=None, visible=False),
        "",
    )


def clear_interview_workspace() -> tuple:
    """Remove prior-user interview data from a reused browser session."""

    return (
        "",
        "",
        "",
        "",
        None,
        "",
        "",
        gr.update(visible=False),
        gr.update(interactive=True),
        gr.update(value="", interactive=True),
        "",
        gr.update(interactive=True),
        "",
    )


def build_app() -> gr.Blocks:
    """Build and return the Gradio application without starting a server."""

    configure_logging()

    css = """
    :root {
        --page-bg: #f6f7fb;
        --panel-bg: #ffffff;
        --ink: #172033;
        --muted: #667085;
        --line: #e4e7ec;
        --brand: #3454d1;
        --brand-dark: #2843ad;
    }

    body, .gradio-container {
        background: var(--page-bg) !important;
        color: var(--ink);
    }

    .gradio-container {
        max-width: 1180px !important;
        padding: 24px 20px 40px !important;
    }

    .auth-shell {
        max-width: 520px;
        margin: 7vh auto 0;
        padding: 34px;
        background: var(--panel-bg);
        border: 1px solid var(--line);
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(16, 24, 40, 0.07);
    }

    .brand-mark {
        color: var(--brand);
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    .auth-title h1 {
        margin: 8px 0 6px;
        font-size: 30px;
        line-height: 1.2;
    }

    .auth-subtitle {
        color: var(--muted);
        margin-bottom: 22px;
    }

    .app-header {
        align-items: center;
        border-bottom: 1px solid var(--line);
        margin-bottom: 18px;
        padding-bottom: 16px;
    }

    .app-header h1 {
        font-size: 24px;
        margin: 0;
    }

    .user-label {
        color: var(--muted);
        text-align: right;
    }

    .section-intro {
        color: var(--muted);
        margin: 2px 0 18px;
    }

    .placeholder-panel {
        background: var(--panel-bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 22px;
    }

    .placeholder-panel h2 {
        margin-top: 0;
        font-size: 19px;
    }

    .resume-score h2 {
        color: #137a52;
        font-size: 25px;
        margin: 0;
    }

    .result-section {
        border-top: 1px solid var(--line);
        margin-top: 16px;
        padding-top: 18px;
    }

    .interview-question {
        border-left: 4px solid var(--brand);
        padding: 4px 0 4px 16px;
    }

    .interview-question h3 {
        font-size: 15px;
        margin: 0 0 8px;
    }

    .history-detail {
        border-top: 1px solid var(--line);
        margin-top: 16px;
        padding-top: 18px;
    }

    .history-detail pre {
        overflow-wrap: anywhere;
        white-space: pre-wrap;
        word-break: break-word;
    }

    .history-detail table {
        display: block;
        max-width: 100%;
        overflow-x: auto;
    }

    .supplement-overlay {
        background: rgba(23, 32, 51, 0.54);
        inset: 0;
        overflow-y: auto;
        padding: 8vh 18px 24px;
        position: fixed;
        z-index: 1000;
    }

    .supplement-modal {
        background: var(--panel-bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        box-shadow: 0 20px 48px rgba(16, 24, 40, 0.2);
        margin: 0 auto;
        max-width: 720px;
        padding: 22px;
        width: 100%;
    }

    .supplement-modal h3 {
        font-size: 16px;
        margin: 0 0 6px;
    }

    .primary-button {
        background: var(--brand) !important;
        border-color: var(--brand) !important;
    }

    .primary-button:hover {
        background: var(--brand-dark) !important;
        border-color: var(--brand-dark) !important;
    }

    @media (max-width: 640px) {
        .gradio-container {
            padding: 20px !important;
        }

        .app-header {
            flex-wrap: wrap !important;
            gap: 8px !important;
        }

        .app-header > * {
            flex: 1 1 100% !important;
            min-width: 0 !important;
            width: 100% !important;
        }

        .user-label {
            text-align: left;
        }
    }
    """

    with gr.Blocks(
        title="AI 求职助手",
        theme=gr.themes.Soft(primary_hue="indigo", neutral_hue="slate"),
        css=css,
    ) as app:
        token_state = gr.State("")
        resume_record_state = gr.State(None)
        interview_session_state = gr.State(None)
        interview_question_state = gr.State("")
        browser_token = gr.Textbox(visible=False)
        with gr.Column(elem_id="login_view", elem_classes="auth-shell") as login_view:
            gr.Markdown("AI JOB ASSISTANT", elem_classes="brand-mark")
            gr.Markdown("# 找到更适合你的下一份工作", elem_classes="auth-title")
            gr.Markdown(
                "从简历诊断到模拟面试，集中管理你的求职准备。",
                elem_classes="auth-subtitle",
            )
            phone = gr.Textbox(
                label="手机号",
                placeholder="请输入 11 位手机号",
                max_lines=1,
            )
            with gr.Row():
                code = gr.Textbox(
                    label="验证码",
                    placeholder="6 位数字",
                    max_lines=1,
                    scale=2,
                )
                send_code = gr.Button("获取验证码", scale=1)
            auth_status = gr.Markdown()
            login_button = gr.Button(
                "登录", variant="primary", elem_classes="primary-button"
            )

        with gr.Column(visible=False, elem_id="app_view") as app_view:
            with gr.Row(elem_classes="app-header"):
                with gr.Column(scale=3):
                    gr.Markdown("# AI 求职助手")
                    gr.Markdown(
                        "把每一次准备，变成更有把握的机会。",
                        elem_classes="section-intro",
                    )
                user_label = gr.Markdown(elem_classes="user-label")
                quota_label = gr.Markdown(elem_classes="user-label")
                logout_button = gr.Button("退出登录", size="sm", scale=1)

            with gr.Tabs():
                with gr.Tab("📄 简历诊断"), gr.Column(elem_classes="placeholder-panel"):
                    gr.Markdown("## 简历诊断")
                    gr.Markdown(
                        "上传 PDF 或粘贴简历文本，获取诊断结果和可编辑的完整优化稿。",
                        elem_classes="section-intro",
                    )
                    with gr.Row():
                        resume_file = gr.File(
                            label="上传 PDF 简历",
                            file_types=[".pdf"],
                            type="filepath",
                            scale=1,
                        )
                        target_position = gr.Textbox(
                            label="目标岗位",
                            placeholder="例如：Java 后端开发工程师",
                            max_lines=1,
                            max_length=100,
                            scale=1,
                        )
                    pasted_resume = gr.Textbox(
                        label="或粘贴简历文本",
                        placeholder="PDF 无法解析时可粘贴文本；填写后将优先使用这里的内容。",
                        lines=7,
                        max_lines=16,
                    )
                    diagnose_button = gr.Button(
                        "开始诊断", variant="primary", elem_classes="primary-button"
                    )
                    resume_status = gr.Markdown()

                    with gr.Column(
                        visible=False,
                        elem_classes="result-section",
                    ) as resume_results:
                        with gr.Row():
                            with gr.Column(scale=1):
                                gr.Markdown("### 岗位匹配度")
                                score_output = gr.Markdown(elem_classes="resume-score")
                            with gr.Column(scale=2):
                                gr.Markdown("### 建议补充的关键词")
                                keyword_output = gr.Markdown()
                        gr.Markdown("### 修改建议与 STAR 示例")
                        suggestions_output = gr.Markdown()
                        optimized_resume = gr.Textbox(
                            label="当前优化稿",
                            info="生成 PDF 前可继续修改；请将方括号占位内容替换为真实信息。",
                            lines=18,
                            max_lines=30,
                            interactive=True,
                        )
                        with gr.Row():
                            generate_resume_button = gr.Button(
                                "生成新版 PDF 简历", variant="primary"
                            )
                            download_resume_button = gr.DownloadButton(
                                "下载 PDF 简历",
                                visible=False,
                            )
                        with gr.Column(
                            visible=False,
                            elem_classes="supplement-overlay",
                        ) as supplement_panel, gr.Column(
                            elem_classes="supplement-modal"
                        ):
                                gr.Markdown("### 补充履历")
                                gr.Markdown(
                                    "补充原简历未写明的项目、职责、成果或证书。只填写真实信息。",
                                    elem_classes="section-intro",
                                )
                                supplemental_experience = gr.Textbox(
                                    label="补充信息（可选）",
                                    placeholder=(
                                        "例如：2025 年负责订单系统缓存改造；个人承担方案设计和上线，"
                                        "接口平均响应时间从 320ms 降至 180ms。"
                                    ),
                                    lines=7,
                                    max_lines=14,
                                    max_length=6000,
                                )
                                resume_photo = gr.File(
                                    label="简历照片（可选，JPEG/PNG/WebP，最大 5MB）",
                                    file_types=[".jpg", ".jpeg", ".png", ".webp"],
                                    type="filepath",
                                )
                                resume_template = gr.Radio(
                                    choices=list(RESUME_TEMPLATE_CHOICES),
                                    value=DEFAULT_RESUME_TEMPLATE_ID,
                                    label="简历模板",
                                    visible=resume_service.enable_templates,
                                )
                                with gr.Row():
                                    cancel_supplement_button = gr.Button("取消")
                                    confirm_generate_button = gr.Button(
                                        "生成简历", variant="primary"
                                    )
                        generation_status = gr.Markdown()

                with gr.Tab("🎯 模拟面试"), gr.Column(elem_classes="placeholder-panel"):
                    gr.Markdown("## 模拟面试")
                    gr.Markdown(
                        "围绕目标岗位进行多轮问答，获得针对性的回答反馈。",
                        elem_classes="section-intro",
                    )
                    interview_position = gr.Textbox(
                        label="目标岗位",
                        placeholder="例如：Java 后端开发工程师",
                        max_lines=1,
                        max_length=100,
                    )
                    with gr.Row():
                        interview_jd = gr.Textbox(
                            label="岗位 JD（可选）",
                            placeholder="粘贴岗位职责和任职要求",
                            lines=7,
                            max_lines=14,
                            max_length=6000,
                        )
                        with gr.Column():
                            interview_resume = gr.Textbox(
                                label="简历核心内容（可选）",
                                placeholder="粘贴与目标岗位相关的项目、职责和技能",
                                lines=7,
                                max_lines=14,
                                max_length=6000,
                            )
                            use_current_resume_button = gr.Button(
                                "使用当前优化稿",
                                size="sm",
                            )
                    with gr.Row(visible=interview_service.enable_modes):
                        interview_mode = gr.Radio(
                            choices=list(
                                get_interview_mode_choices(
                                    interview_service.interview_policy_version
                                    if interview_service.enable_modes
                                    else INTERVIEW_STANDARD_POLICY_VERSION
                                )
                            ),
                            value=DEFAULT_INTERVIEW_MODE,
                            label="面试节奏",
                        )
                        interview_feedback_mode = gr.Radio(
                            choices=list(FEEDBACK_MODE_CHOICES),
                            value=DEFAULT_FEEDBACK_MODE,
                            label="反馈方式",
                        )
                    with gr.Row():
                        start_interview_button = gr.Button(
                            "开始面试",
                            variant="primary",
                            elem_classes="primary-button",
                        )
                        restart_interview_button = gr.Button("重新开始")
                    interview_status = gr.Markdown()
                    with gr.Column(
                        visible=False,
                        elem_classes="result-section",
                    ) as interview_results:
                        interview_progress = gr.Markdown()
                        interview_question = gr.Markdown(
                            elem_classes="interview-question"
                        )
                        interview_answer = gr.Textbox(
                            label="你的回答",
                            placeholder="结合具体情境、行动和结果作答",
                            lines=7,
                            max_lines=14,
                            max_length=6000,
                        )
                        submit_answer_button = gr.Button(
                            "提交回答",
                            variant="primary",
                        )
                        interview_feedback = gr.Markdown()

                with gr.Tab("📊 我的记录"), gr.Column(elem_classes="placeholder-panel"):
                    gr.Markdown("## 我的记录")
                    gr.Markdown(
                        "查看最近 5 条简历诊断和模拟面试记录。",
                        elem_classes="section-intro",
                    )
                    refresh_history_button = gr.Button("刷新记录", size="sm")
                    history_status = gr.Markdown()
                    gr.Markdown("### 简历诊断")
                    resume_history = gr.Dataframe(
                        headers=RESUME_HISTORY_HEADERS,
                        value=[],
                        datatype=["number", "str", "str", "str"],
                        type="array",
                        interactive=False,
                        wrap=False,
                        column_widths=[70, 150, 180, 100],
                        height=260,
                        elem_classes="history-table",
                    )
                    gr.Markdown("### 模拟面试")
                    interview_history = gr.Dataframe(
                        headers=INTERVIEW_HISTORY_HEADERS,
                        value=[],
                        datatype=["number", "str", "str", "str", "str"],
                        type="array",
                        interactive=False,
                        wrap=False,
                        column_widths=[70, 150, 180, 110, 100],
                        height=260,
                        elem_classes="history-table",
                    )
                    history_detail = gr.Markdown(elem_classes="history-detail")

        send_code.click(request_code, inputs=phone, outputs=auth_status)
        auth_outputs = [
            login_view,
            app_view,
            auth_status,
            user_label,
            quota_label,
            token_state,
            browser_token,
        ]
        history_outputs = [
            resume_history,
            interview_history,
            history_status,
            history_detail,
        ]
        login_button.click(
            login,
            inputs=[phone, code],
            outputs=auth_outputs,
            api_name=False,
        ).then(
            fn=None,
            inputs=browser_token,
            outputs=browser_token,
            js=WRITE_TOKEN_JS,
        ).then(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )
        logout_button.click(
            logout,
            inputs=token_state,
            outputs=[*auth_outputs, phone, code],
            api_name=False,
        ).then(
            fn=None,
            inputs=browser_token,
            outputs=browser_token,
            js=WRITE_TOKEN_JS,
        ).then(
            clear_resume_workspace,
            outputs=[
                resume_file,
                target_position,
                pasted_resume,
                resume_status,
                score_output,
                keyword_output,
                suggestions_output,
                optimized_resume,
                resume_record_state,
                resume_results,
                supplemental_experience,
                resume_photo,
                supplement_panel,
                download_resume_button,
                generation_status,
            ],
            api_name=False,
        ).then(
            clear_interview_workspace,
            outputs=[
                interview_position,
                interview_jd,
                interview_resume,
                interview_status,
                interview_session_state,
                interview_progress,
                interview_question,
                interview_results,
                start_interview_button,
                interview_answer,
                interview_feedback,
                submit_answer_button,
                interview_question_state,
            ],
            api_name=False,
        ).then(
            clear_history_workspace,
            outputs=history_outputs,
            api_name=False,
        )
        app.load(
            restore_login,
            inputs=browser_token,
            outputs=auth_outputs,
            js=READ_TOKEN_JS,
            api_name=False,
        ).then(
            fn=None,
            inputs=browser_token,
            outputs=browser_token,
            js=WRITE_TOKEN_JS,
        ).then(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )

        diagnose_button.click(
            diagnose_resume_callback,
            inputs=[resume_file, pasted_resume, target_position, token_state],
            outputs=[
                resume_status,
                score_output,
                keyword_output,
                suggestions_output,
                optimized_resume,
                resume_record_state,
                quota_label,
                resume_results,
                supplemental_experience,
                resume_photo,
                supplement_panel,
                download_resume_button,
                generation_status,
            ],
            api_name=False,
        ).then(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )
        generate_resume_button.click(
            open_supplement_callback,
            inputs=resume_record_state,
            outputs=[
                supplement_panel,
                supplemental_experience,
                download_resume_button,
                generation_status,
            ],
            api_name=False,
        )
        cancel_supplement_button.click(
            cancel_supplement_callback,
            outputs=[supplement_panel, supplemental_experience, generation_status],
            api_name=False,
        )

        pending_outputs = [
            cancel_supplement_button,
            confirm_generate_button,
            generation_status,
            download_resume_button,
        ]
        generation_outputs = [
            supplement_panel,
            supplemental_experience,
            download_resume_button,
            generation_status,
            optimized_resume,
            cancel_supplement_button,
            confirm_generate_button,
        ]
        confirm_generate_button.click(
            begin_resume_generation,
            outputs=pending_outputs,
            queue=False,
            api_name=False,
        ).then(
            generate_resume_callback,
            inputs=[
                token_state,
                resume_record_state,
                optimized_resume,
                supplemental_experience,
                resume_photo,
                resume_template,
            ],
            outputs=generation_outputs,
            trigger_mode="once",
            concurrency_limit=1,
            concurrency_id="resume-pdf-generation",
            api_name=False,
        )
        use_current_resume_button.click(
            use_optimized_resume_callback,
            inputs=[optimized_resume, target_position, interview_position],
            outputs=[interview_resume, interview_position, interview_status],
            api_name=False,
        )
        start_interview_button.click(
            begin_interview_start,
            outputs=[start_interview_button, interview_status],
            queue=False,
            api_name=False,
        ).then(
            start_interview_callback,
            inputs=[
                interview_position,
                interview_jd,
                interview_resume,
                token_state,
                interview_mode,
                interview_feedback_mode,
            ],
            outputs=[
                interview_status,
                interview_session_state,
                interview_progress,
                interview_question,
                interview_results,
                quota_label,
                start_interview_button,
                interview_answer,
                submit_answer_button,
                interview_feedback,
                interview_question_state,
            ],
            trigger_mode="once",
            concurrency_limit=4,
            concurrency_id="interview-start",
            api_name=False,
        ).then(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )
        submit_answer_button.click(
            begin_answer_submission,
            outputs=[submit_answer_button, interview_feedback],
            queue=False,
            api_name=False,
        ).then(
            submit_interview_answer_callback,
            inputs=[
                interview_answer,
                interview_session_state,
                token_state,
                interview_question_state,
            ],
            outputs=[
                interview_feedback,
                interview_answer,
                submit_answer_button,
                interview_progress,
                interview_question,
                interview_question_state,
                interview_status,
            ],
            trigger_mode="once",
            concurrency_limit=4,
            concurrency_id="interview-answer",
            api_name=False,
        ).then(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )
        restart_interview_button.click(
            clear_interview_workspace,
            outputs=[
                interview_position,
                interview_jd,
                interview_resume,
                interview_status,
                interview_session_state,
                interview_progress,
                interview_question,
                interview_results,
                start_interview_button,
                interview_answer,
                interview_feedback,
                submit_answer_button,
                interview_question_state,
            ],
            queue=False,
            api_name=False,
        )
        refresh_history_button.click(
            load_history_callback,
            inputs=token_state,
            outputs=history_outputs,
            api_name=False,
        )
        resume_history.select(
            load_resume_history_detail_callback,
            inputs=token_state,
            outputs=history_detail,
            api_name=False,
        )
        interview_history.select(
            load_interview_history_detail_callback,
            inputs=token_state,
            outputs=history_detail,
            api_name=False,
        )

    return app.queue(max_size=100, default_concurrency_limit=4)


if __name__ == "__main__":
    try:
        build_app().launch()
    except Exception as exc:
        report_exception(logger, "frontend.launch", exc)
        raise
