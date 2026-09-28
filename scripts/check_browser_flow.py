"""Exercise Chromium + FastAPI + Gradio + Redis + SQLite + persistent Chroma.

Default uses fixed model/embedding responses. --live calls configured providers
with synthetic input only. All business state and processes are isolated.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

ROOT = Path(__file__).resolve().parents[1]


def serve(port: int, live: bool) -> None:
    import uvicorn

    import frontend
    from pbl_jobs_finder.config import get_settings
    from pbl_jobs_finder.server import create_app

    if not live:
        from pbl_jobs_finder.modules.question_bank import load_question_bank
        from pbl_jobs_finder.vector_store import ChromaVectorStore
        from tests.test_interview_agent import FakeChatClient, InterviewServiceTests
        from tests.test_resume_diagnosis import (
            _model_response,
            _resume_document_response,
        )
        from tests.test_vector_store import KeywordEmbedding

        store = ChromaVectorStore(KeywordEmbedding(), persist_directory=get_settings().chroma_dir)
        store.add_question_bank(load_question_bank())
        frontend.resume_service.chat_client = FakeChatClient(_resume_document_response())
        frontend.resume_service.chat_client.responses = [_model_response(), _resume_document_response()]
        questions = [
            "请介绍你开发过的系统以及你负责的主要工作？",
            "请说明你会如何设计消息消费的幂等机制，并处理重复消息？",
            "你如何分析数据库的慢查询并选择合适的索引？",
            "如果缓存和数据库发生不一致，你会如何处理？",
            "请总结你解决复杂线上故障的方法和复盘方式？",
        ]
        responses = [questions[0]]
        for number in range(5):
            responses.append(json.dumps({
                "feedback": "回答清楚地说明了分析步骤，建议补充具体的数据和验证方式。",
                "needs_follow_up": False,
                "next_question": questions[number + 1] if number < 4 else "",
            }, ensure_ascii=False))
        responses.append(InterviewServiceTests._report_json())
        frontend.interview_service.chat_client = FakeChatClient(responses[0])
        frontend.interview_service.chat_client.responses = responses
        frontend.interview_service.vector_store = store
    uvicorn.run(create_app(), host="127.0.0.1", port=port, access_log=False)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(check, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (requests.RequestException, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Acceptance condition timed out")


def run(live: bool, output: Path) -> dict:
    from playwright.sync_api import expect, sync_playwright
    from redis import Redis

    from pbl_jobs_finder.config import get_settings
    from scripts.run_acceptance_demo import ANSWER, POSITION, SAMPLE_RESUME
    from tests.test_resume_diagnosis import _write_text_pdf

    settings = get_settings()
    output.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with TemporaryDirectory(prefix="pbl-browser-") as directory:
        root = Path(directory)
        redis_port, app_port = free_port(), free_port()
        redis = subprocess.Popen(
            [shutil.which("redis-server"), "--bind", "127.0.0.1", "--port", str(redis_port),
             "--save", "", "--appendonly", "yes", "--appendfsync", "always", "--dir", str(root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        client = Redis(host="127.0.0.1", port=redis_port, decode_responses=True)
        app = None
        base = f"http://127.0.0.1:{app_port}"
        env = dict(os.environ, DATA_DIR=str(root / "data"), DATABASE_URL="", UPLOADS_DIR="",
                   EXPORTS_DIR="", LOG_DIR="", CHROMA_DIR=str(root / "chroma"),
                   STATE_BACKEND="redis", REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
                   REDIS_PREFIX="browser:", GRADIO_TEMP_DIR=str(root / "gradio"),
                   GRADIO_ANALYTICS_ENABLED="False", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   ENABLE_RESUME_DIMENSIONS="False", ENABLE_INTERVIEW_MODES="False",
                   PLAYWRIGHT_BROWSERS_PATH=str(ROOT / ".playwright-browsers"))
        if live:
            shutil.copytree(settings.chroma_dir, root / "chroma")
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright-browsers"))
        log_path = output / "server.log"
        metrics = {"mode": "live" if live else "fixed", "stages": {}, "passed": False}
        try:
            wait_until(client.ping)
            with log_path.open("w", encoding="utf-8") as log:
                def start_app():
                    command = [sys.executable, "-m", "scripts.check_browser_flow", "--serve", str(app_port)]
                    if live:
                        command.append("--live")
                    process = subprocess.Popen(command, env=env, cwd=ROOT, stdout=log,
                                               stderr=log, creationflags=flags)
                    try:
                        def ready():
                            if process.poll() is not None:
                                raise RuntimeError("Application exited; inspect isolated server.log")
                            return requests.get(base + "/health/ready", timeout=2).status_code == 200
                        wait_until(ready, 60)
                    except BaseException:
                        process.terminate()
                        process.wait(timeout=20)
                        raise
                    return process

                app = start_app()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context()
                    page = context.new_page()
                    page.set_default_timeout(180000 if live else 30000)
                    page.goto(base)
                    expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
                    page.get_by_label("手机号", exact=True).fill("13900000028")
                    page.get_by_role("button", name="获取验证码").click()
                    wait_until(lambda: re.search(r"verification code for \d+: (\d{6})", log_path.read_text(encoding="utf-8")))
                    code = re.findall(r"verification code for \d+: (\d{6})", log_path.read_text(encoding="utf-8"))[-1]
                    page.get_by_label("验证码", exact=True).fill(code)
                    page.get_by_role("button", name="登录", exact=True).click()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.wait_for_function("localStorage.getItem('pbl_jobs_finder.auth_token')")
                    token = page.evaluate("localStorage.getItem('pbl_jobs_finder.auth_token')")
                    headers = {"Authorization": f"Bearer {token}"}
                    # Upload a real text PDF, rather than invoking callbacks directly.
                    source = root / "synthetic.pdf"
                    _write_text_pdf(source, "Python backend engineer with five years of experience in APIs, databases and testing.")
                    with page.expect_response(lambda response: "/upload" in response.url and response.request.method == "POST") as upload:
                        page.locator('input[type="file"]').first.set_input_files(str(source))
                    assert upload.value.status == 200
                    page.screenshot(path=str(output / "upload.png"), full_page=True)
                    page.get_by_label("目标岗位", exact=True).first.fill(POSITION)
                    if live:
                        page.get_by_label("或粘贴简历文本", exact=True).fill(SAMPLE_RESUME)
                    started = time.perf_counter()
                    page.get_by_role("button", name="开始诊断", exact=True).click()
                    expect(page.get_by_label("当前优化稿", exact=False)).to_be_visible(timeout=180000)
                    metrics["stages"]["diagnosis_seconds"] = round(time.perf_counter() - started, 3)
                    page.get_by_role("button", name="生成新版 PDF 简历").click()
                    started = time.perf_counter()
                    page.get_by_role("button", name="生成简历", exact=True).click()
                    link = page.get_by_role("link", name="下载 PDF 简历")
                    expect(link).to_be_visible(timeout=180000)
                    metrics["stages"]["pdf_seconds"] = round(time.perf_counter() - started, 3)
                    download_url = link.get_attribute("href")
                    with page.expect_download() as event:
                        link.click()
                    downloaded = Path(event.value.path())
                    assert downloaded.read_bytes().startswith(b"%PDF-")
                    assert requests.get(base + download_url, timeout=5).status_code == 401
                    page.screenshot(path=str(output / "resume.png"), full_page=True)
                    page.get_by_role("tab", name="🎯 模拟面试").click()
                    page.get_by_label("目标岗位", exact=True).last.fill(POSITION)
                    page.get_by_role("radio", name="入门", exact=True).check()
                    started = time.perf_counter()
                    page.get_by_role("button", name="开始面试", exact=True).click()
                    expect(page.get_by_label("你的回答", exact=True)).to_be_visible(timeout=180000)
                    metrics["stages"]["first_question_seconds"] = round(time.perf_counter() - started, 3)
                    started = time.perf_counter()
                    for number in range(5):
                        page.get_by_label("你的回答", exact=True).fill(ANSWER)
                        page.get_by_role("button", name="提交回答", exact=True).click()
                        if number < 4:
                            expect(page.get_by_text(f"已进入第 {number + 2} 题。", exact=True)).to_be_visible(timeout=180000)
                        else:
                            expect(page.get_by_text("本轮面试已完成", exact=True)).to_be_visible(timeout=180000)
                    metrics["stages"]["answers_report_seconds"] = round(time.perf_counter() - started, 3)
                    page.screenshot(path=str(output / "report.png"), full_page=True)
                    history = requests.get(base + "/api/history", headers=headers, timeout=5).json()
                    assert len(history["resumes"]) == len(history["interviews"]) == 1
                    assert requests.get(base + "/api/quota", headers=headers, timeout=5).json()["used"] == 2
                    page.get_by_role("tab", name="📊 我的记录").click()
                    expect(page.get_by_text("已完成", exact=True).first).to_be_visible()
                    page.screenshot(path=str(output / "history.png"), full_page=True)
                    # Restart the actual application, retaining Redis and SQLite.
                    app.terminate()
                    app.wait(timeout=20)
                    app = start_app()
                    page.reload()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    assert requests.get(base + "/api/quota", headers=headers, timeout=5).json()["used"] == 2
                    assert requests.get(base + "/api/history", headers=headers, timeout=5).json() == history
                    assert requests.get(base + download_url, headers=headers, timeout=5).status_code == 200
                    page.get_by_role("button", name="退出登录").click()
                    expect(page.get_by_role("button", name="登录", exact=True)).to_be_visible()
                    assert requests.get(base + "/api/history", headers=headers, timeout=5).status_code == 401
                    assert requests.get(base + download_url, headers=headers, timeout=5).status_code == 401
                    metrics.update(passed=True, answer_submissions=5, quota_used=2, restart_recovered=True)
                    browser.close()
        finally:
            if app is not None:
                app.terminate()
                app.wait(timeout=20)
            client.close()
            redis.terminate()
            redis.wait(timeout=10)
            (output / "result.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "browser-2026-09-28")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.live)
    else:
        print(json.dumps(run(args.live, args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
