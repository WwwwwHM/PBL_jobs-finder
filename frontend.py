"""Gradio frontend with token-backed login persistence and shared quota."""

from __future__ import annotations

import gradio as gr

from pbl_jobs_finder.modules.auth import (
    revoke_token,
    send_verification_code,
    verify_login,
    verify_token,
)
from pbl_jobs_finder.modules.quota import AuthenticationError, quota_service

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
                        "上传简历并输入目标岗位，获取匹配度、关键词缺口和 STAR 优化建议。",
                        elem_classes="section-intro",
                    )
                    gr.Markdown("简历诊断功能将在业务模块接入后启用。")

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

    return app


if __name__ == "__main__":
    build_app().launch()
