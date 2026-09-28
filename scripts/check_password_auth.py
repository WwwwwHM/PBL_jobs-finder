"""Exercise password registration and login in Chromium with isolated state."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright-browsers"))
    with TemporaryDirectory(prefix="pbl-password-") as directory:
        root = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(
            os.environ, DATA_DIR=str(root / "data"), DATABASE_URL="", UPLOADS_DIR="",
            EXPORTS_DIR="", LOG_DIR="", CHROMA_DIR=str(root / "chroma"),
            STATE_BACKEND="memory", GRADIO_TEMP_DIR=str(root / "gradio"),
            GRADIO_ANALYTICS_ENABLED="False", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
        )
        base = f"http://127.0.0.1:{port}"
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "pbl_jobs_finder.server:create_app",
                 "--factory", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                for _ in range(200):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated app exited during startup")
                    try:
                        if requests.get(base + "/health/live", timeout=1).ok:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated app did not start")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    page.set_default_timeout(15000)
                    page.goto(base)
                    expect(page.get_by_role("button", name="密码登录", exact=True)).to_be_visible()
                    page.get_by_label("手机号", exact=True).fill("13900000028")
                    page.get_by_role("tab", name="注册账号", exact=True).click()
                    page.get_by_label("设置密码", exact=True).fill("BrowserPass123")
                    page.get_by_label("确认密码", exact=True).fill("Mismatch123")
                    page.get_by_role("button", name="注册并登录").click()
                    expect(page.get_by_text("两次输入的密码不一致", exact=True)).to_be_visible()
                    expect(page.get_by_label("设置密码", exact=True)).to_have_value("")
                    page.get_by_label("设置密码", exact=True).fill("BrowserPass123")
                    page.get_by_label("确认密码", exact=True).fill("BrowserPass123")
                    page.get_by_role("button", name="注册并登录").click()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.wait_for_function("!!localStorage.getItem('pbl_jobs_finder.auth_token')")
                    token = page.evaluate("localStorage.getItem('pbl_jobs_finder.auth_token')")
                    page.reload()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.get_by_role("button", name="退出登录").click()
                    expect(page.get_by_role("tab", name="密码登录", exact=True)).to_be_visible()
                    page.wait_for_function("!localStorage.getItem('pbl_jobs_finder.auth_token')")
                    assert requests.get(base + "/api/history", headers={"Authorization": f"Bearer {token}"}, timeout=5).status_code == 401
                    page.get_by_role("tab", name="密码登录", exact=True).click()
                    page.get_by_label("手机号", exact=True).fill("13900000028")
                    page.get_by_label("密码", exact=True).fill("WrongPass123")
                    page.get_by_role("button", name="密码登录", exact=True).click()
                    expect(page.get_by_text("手机号或密码错误", exact=True)).to_be_visible()
                    expect(page.get_by_label("密码", exact=True)).to_have_value("")
                    page.get_by_label("密码", exact=True).fill("BrowserPass123")
                    page.get_by_label("密码", exact=True).press("Enter")
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.get_by_role("button", name="退出登录").click()
                    expect(page.get_by_role("tab", name="验证码登录", exact=True)).to_be_visible()
                    page.get_by_role("tab", name="验证码登录", exact=True).click()
                    page.get_by_label("手机号", exact=True).fill("13900000029")
                    page.get_by_role("button", name="获取验证码").click()
                    expect(page.get_by_text("验证码已发送，请查收", exact=True)).to_be_visible()
                    matches = re.findall(r"verification code for 13900000029: (\d{6})", (root / "server.log").read_text(encoding="utf-8"))
                    page.get_by_label("验证码", exact=True).fill(matches[-1])
                    page.get_by_role("button", name="登录", exact=True).click()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.get_by_text("账号设置 · 设置登录密码", exact=True).click()
                    page.get_by_label("新密码", exact=True).fill("LegacyPass123")
                    page.get_by_label("确认新密码", exact=True).fill("LegacyPass123")
                    page.get_by_role("button", name="设置密码", exact=True).click()
                    expect(page.get_by_text("密码设置成功，下次可使用手机号和密码登录", exact=True)).to_be_visible()
                    page.get_by_role("button", name="退出登录").click()
                    expect(page.get_by_role("tab", name="密码登录", exact=True)).to_be_visible()
                    page.get_by_role("tab", name="密码登录", exact=True).click()
                    page.get_by_label("手机号", exact=True).fill("13900000029")
                    page.get_by_label("密码", exact=True).fill("LegacyPass123")
                    page.get_by_role("button", name="密码登录", exact=True).click()
                    expect(page.get_by_role("button", name="退出登录")).to_be_visible()
                    page.get_by_role("button", name="退出登录").click()
                    expect(page.get_by_role("tab", name="密码登录", exact=True)).to_be_visible()
                    page.set_viewport_size({"width": 390, "height": 844})
                    expect(page.get_by_role("button", name="密码登录", exact=True)).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    output = ROOT / "output" / "password-auth-mobile.png"
                    output.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(output), full_page=True)
                    browser.close()
                    print("PASS: registration, validation, password login, reload, logout, legacy password setup, mobile layout")
            finally:
                if os.name == "nt" and process.poll() is None:
                    # The Windows venv launcher may own a child Python process.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=True, stdout=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                elif process.poll() is None:
                    process.terminate()
                process.wait(timeout=20)


if __name__ == "__main__":
    main()
