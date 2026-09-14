"""Gradio frontend with token-backed login persistence and shared quota."""

from __future__ import annotations

import gradio as gr

from pbl_jobs_finder.modules.auth import (
    revoke_token,
    send_verification_code,
    verify_login,
    verify_token,
)
from pbl_jobs_finder.modules.quota import (
    AuthenticationError,
    QuotaExceededError,
    quota_service,
)
from pbl_jobs_finder.modules.resume_diagnosis import (
    ResumeAccessError,
    ResumeDiagnosisService,
    ResumeParseError,
    ResumeResponseError,
    ResumeValidationError,
)
from pbl_jobs_finder.modules.resume_export import ResumeExportError
from pbl_jobs_finder.utils.llm_client import LLMConfigurationError, LLMServiceError

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


def request_code(phone: str) -> str:
    """Validate the phone field and return a frontend status message.

    Code generation and console output are handled by the authentication
    service; this callback only translates its result into UI text.
    """

    phone = (phone or "").strip()
    _, message = send_verification_code(phone)
    return message


def login(phone: str, code: str) -> tuple:
    """Authenticate and provide the token to server state and the browser."""

    token, message = verify_login((phone or "").strip(), (code or "").strip())
    if token is None:
        return _logged_out(message)
    return restore_login(token)


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

    user = verify_token(token)
    if user is None:
        return _logged_out("登录已失效，请重新登录" if token else "")
    try:
        status = quota_service.status(token)
    except AuthenticationError:
        return _logged_out("登录已失效，请重新登录")
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

    revoke_token(token)
    return (*_logged_out("已退出登录"), "", "")


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
        return (
            f"**诊断未完成：** {exc}",
            "",
            "",
            "",
            "",
            None,
            _quota_label(token),
            gr.update(visible=False),
            gr.update(value=None, visible=False),
            "",
        )

    diagnosis = outcome.diagnosis
    keywords = "、".join(diagnosis.missing_keywords) or "未发现明显缺失关键词"
    suggestions = (
        f"{diagnosis.suggestions}\n\n### STAR 改写示例\n{diagnosis.star_examples}"
    )
    return (
        "诊断已完成，优化稿可继续编辑后导出。",
        f"## {diagnosis.score} / 100",
        keywords,
        suggestions,
        diagnosis.optimized_text,
        outcome.record_id,
        f"今日剩余 {remaining} / {quota_service.limit} 次",
        gr.update(visible=True),
        gr.update(value=None, visible=False),
        "",
    )


def generate_resume_callback(
    token: str,
    record_id: int | str | None,
    optimized_text: str,
) -> tuple:
    """Generate a downloadable DOCX from the user's final edited draft."""

    try:
        destination = resume_service.export_optimized_resume(
            token=token,
            record_id=record_id,
            optimized_text=optimized_text,
        )
    except (
        AuthenticationError,
        ResumeAccessError,
        ResumeExportError,
        ResumeValidationError,
        ValueError,
    ) as exc:
        return gr.update(value=None, visible=False), f"**生成失败：** {exc}"
    return (
        gr.update(value=str(destination), visible=True),
        "新版 Word 简历已生成，可直接下载。",
    )


def _quota_label(token: str) -> str:
    try:
        status = quota_service.status(token)
    except AuthenticationError:
        return ""
    return f"今日剩余 {status.remaining} / {status.limit} 次"


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
        gr.update(value=None, visible=False),
        "",
    )


def build_app() -> gr.Blocks:
    """Build and return the Gradio application without starting a server."""

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
                            label="完整优化稿",
                            info="可在生成前继续修改；请将方括号占位内容替换为真实信息。",
                            lines=18,
                            max_lines=30,
                            interactive=True,
                        )
                        with gr.Row():
                            generate_resume_button = gr.Button(
                                "生成新版简历", variant="primary"
                            )
                            download_resume_button = gr.DownloadButton(
                                "下载 Word 简历",
                                visible=False,
                            )
                        export_status = gr.Markdown()

                with gr.Tab("🎯 模拟面试"), gr.Column(elem_classes="placeholder-panel"):
                    gr.Markdown("## 模拟面试")
                    gr.Markdown(
                        "围绕目标岗位进行多轮问答，获得针对性的回答反馈。",
                        elem_classes="section-intro",
                    )
                    gr.Markdown("模拟面试功能将在业务模块接入后启用。")

                with gr.Tab("📊 我的记录"), gr.Column(elem_classes="placeholder-panel"):
                    gr.Markdown("## 我的记录")
                    gr.Markdown(
                        "查看历史简历诊断、模拟面试和能力分析结果。",
                        elem_classes="section-intro",
                    )
                    gr.Markdown("历史记录功能将在数据模块接入后启用。")

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
                download_resume_button,
                export_status,
            ],
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
                download_resume_button,
                export_status,
            ],
            api_name=False,
        )
        generate_resume_button.click(
            generate_resume_callback,
            inputs=[token_state, resume_record_state, optimized_resume],
            outputs=[download_resume_button, export_status],
            api_name=False,
        )

    return app


if __name__ == "__main__":
    build_app().launch()
