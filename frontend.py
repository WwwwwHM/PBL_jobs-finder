"""Gradio frontend shell for the AI job assistant MVP.

The callbacks in this module intentionally keep authentication and business
logic out of the UI layer. They provide a small demo flow so the page can be
reviewed before the service modules are implemented.
"""

from __future__ import annotations

import re

import gradio as gr


PHONE_PATTERN = re.compile(r"^1\d{10}$")
CODE_PATTERN = re.compile(r"^\d{6}$")


def request_code(phone: str) -> str:
    """Validate the phone field and return a frontend status message.

    The actual code generation and console output belong to the auth module in
    the next task. Keeping this validation here makes the page usable while
    that module is being built.
    """

    phone = (phone or "").strip()
    if not PHONE_PATTERN.fullmatch(phone):
        return "请输入有效的 11 位手机号"
    return "验证码已发送，请查收（当前为前端演示）"


def demo_login(phone: str, code: str) -> tuple[gr.update, gr.update, str, str]:
    """Toggle the main application view for the frontend-only demo."""

    phone = (phone or "").strip()
    code = (code or "").strip()
    if not PHONE_PATTERN.fullmatch(phone):
        return gr.update(), gr.update(), "登录失败：请输入有效的 11 位手机号", ""
    if not CODE_PATTERN.fullmatch(code):
        return gr.update(), gr.update(), "登录失败：请输入 6 位数字验证码", ""

    return (
        gr.update(visible=False),
        gr.update(visible=True),
        "",
        f"求职者 · {phone}",
    )


def logout() -> tuple[gr.update, gr.update, str, str, str]:
    """Return the UI to the login state."""

    return (
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        "",
        "已退出登录",
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

    .primary-button {
        background: var(--brand) !important;
        border-color: var(--brand) !important;
    }

    .primary-button:hover {
        background: var(--brand-dark) !important;
        border-color: var(--brand-dark) !important;
    }
    """

    with gr.Blocks(
        title="AI 求职助手",
        theme=gr.themes.Soft(primary_hue="indigo", neutral_hue="slate"),
        css=css,
    ) as app:
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
            login_button = gr.Button("登录", variant="primary", elem_classes="primary-button")

        with gr.Column(visible=False, elem_id="app_view") as app_view:
            with gr.Row(elem_classes="app-header"):
                with gr.Column(scale=3):
                    gr.Markdown("# AI 求职助手")
                    gr.Markdown(
                        "把每一次准备，变成更有把握的机会。",
                        elem_classes="section-intro",
                    )
                user_label = gr.Markdown(elem_classes="user-label")
                logout_button = gr.Button("退出登录", size="sm", scale=1)

            with gr.Tabs():
                with gr.Tab("📄 简历诊断"):
                    with gr.Column(elem_classes="placeholder-panel"):
                        gr.Markdown("## 简历诊断")
                        gr.Markdown(
                            "上传简历并输入目标岗位，获取匹配度、关键词缺口和 STAR 优化建议。",
                            elem_classes="section-intro",
                        )
                        gr.Markdown("简历诊断功能将在业务模块接入后启用。")

                with gr.Tab("🎯 模拟面试"):
                    with gr.Column(elem_classes="placeholder-panel"):
                        gr.Markdown("## 模拟面试")
                        gr.Markdown(
                            "围绕目标岗位进行多轮问答，获得针对性的回答反馈。",
                            elem_classes="section-intro",
                        )
                        gr.Markdown("模拟面试功能将在业务模块接入后启用。")

                with gr.Tab("📊 我的记录"):
                    with gr.Column(elem_classes="placeholder-panel"):
                        gr.Markdown("## 我的记录")
                        gr.Markdown(
                            "查看历史简历诊断、模拟面试和能力分析结果。",
                            elem_classes="section-intro",
                        )
                        gr.Markdown("历史记录功能将在数据模块接入后启用。")

        send_code.click(request_code, inputs=phone, outputs=auth_status)
        login_button.click(
            demo_login,
            inputs=[phone, code],
            outputs=[login_view, app_view, auth_status, user_label],
        )
        logout_button.click(
            logout,
            outputs=[login_view, app_view, user_label, code, auth_status],
        )

    return app


if __name__ == "__main__":
    build_app().launch()
