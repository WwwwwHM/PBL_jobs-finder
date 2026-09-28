"""Verify a locked install from an isolated source copy and fresh virtualenv.

Requires uv, redis-server and installed Chromium. --live explicitly enables
question-bank embedding and the synthetic live browser demo. No operational
database is copied. Evidence stays under tmp/; source .env is never copied.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values
from redis import Redis

from scripts.check_browser_flow import free_port, wait_until
from scripts.check_rollback import file_manifest

ROOT = Path(__file__).resolve().parents[1]


def run(output: Path) -> dict:
    if output.exists():
        raise ValueError("Use a new output directory; existing evidence is never overwritten")
    output.mkdir(parents=True)
    source = output / "source"
    source.mkdir()
    for name in ("pyproject.toml", "uv.lock", "requirements.txt", "README.md", "frontend.py"):
        shutil.copy2(ROOT / name, source / name)
    for name in ("src", "scripts", "tests"):
        shutil.copytree(ROOT / name, source / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = file_manifest(source)
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    env = dict(os.environ)
    # Process settings override dotenv, matching the application. Only service
    # and policy settings are inherited; every writable path is isolated below.
    for key, value in dotenv_values(ROOT / ".env").items():
        if value is not None:
            env.setdefault(key, value)
    env.update(UV_PROJECT_ENVIRONMENT=str(source / ".venv"),
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONPATH="",
               DATA_DIR=str(output / "data"), DATABASE_URL="", UPLOADS_DIR="",
               EXPORTS_DIR="", CHROMA_DIR="", LOG_DIR="", STATE_BACKEND="redis",
               REDIS_PREFIX="clean-release:", GRADIO_ANALYTICS_ENABLED="False",
               GRADIO_TEMP_DIR=str(output / "gradio"), RUN_PLAYWRIGHT_PDF_TESTS="1",
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / ".playwright-browsers"))
    metrics = {"passed": False, "stages": {}, "source_files": len(manifest)}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def command(label, args, timeout=600):
        started = time.perf_counter()
        with (output / f"{label}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(args, cwd=source, env=env, stdout=log, stderr=log,
                                    creationflags=flags, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"{label} failed; inspect its isolated log")
        metrics["stages"][label] = round(time.perf_counter() - started, 3)
        print(f"PASS {label}", flush=True)

    redis = app = client = None
    try:
        command("install", [shutil.which("uv"), "sync", "--locked", "--offline",
                            "--cache-dir", str(ROOT / ".uv-cache"), "--python", sys.executable])
        python = str(source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        command("dependencies", [python, "scripts/check_dependencies.py"])
        command("pip-check", [shutil.which("uv"), "pip", "check", "--python", python,
                              "--cache-dir", str(ROOT / ".uv-cache")])
        redis_port = free_port()
        redis_dir = output / "redis"
        redis_dir.mkdir()
        env["REDIS_URL"] = f"redis://127.0.0.1:{redis_port}/0"
        redis = subprocess.Popen(
            [shutil.which("redis-server"), "--bind", "127.0.0.1", "--port", str(redis_port),
             "--save", "", "--appendonly", "yes", "--appendfsync", "always", "--dir", str(redis_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        client = Redis(port=redis_port, socket_timeout=2, socket_connect_timeout=2)
        wait_until(client.ping)
        for index in (1, 2):
            command(f"initialize-{index}", [python, "-m", "pbl_jobs_finder"])
            command(f"question-import-{index}", [python, "scripts/import_question_bank.py"])
        command("preflight", [python, "scripts/check_deployment.py", "--release"])
        command("tests", [python, "-m", "unittest", "discover", "-s", "tests", "-v"])
        port = free_port()
        with (output / "server.log").open("w", encoding="utf-8") as log:
            app = subprocess.Popen(
                [python, "-m", "uvicorn", "pbl_jobs_finder.server:create_app", "--factory",
                 "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
                cwd=source, env=env, stdout=log, stderr=log, creationflags=flags)
            base = f"http://127.0.0.1:{port}"
            def ready():
                if app.poll() is not None:
                    raise RuntimeError("Clean deployment exited; inspect server.log")
                return requests.get(base + "/health/ready", timeout=2).status_code == 200
            wait_until(ready, 60)
            responses = {}
            for path, expected in (("/", 200), ("/health/live", 200),
                                   ("/health/ready", 200), ("/api/history", 401)):
                response = requests.get(base + path, timeout=10)
                assert response.status_code == expected
                responses[path] = response.status_code
            metrics["http"] = responses
            app.terminate()
            app.wait(timeout=20)
            app = None
        command("browser-live-rollback", [python, "-m", "scripts.check_browser_flow", "--live",
                                          "--rollback", "--output", str(output / "browser")], 900)
        metrics["browser"] = json.loads((output / "browser/result.json").read_text(encoding="utf-8"))
        metrics["passed"] = True
    finally:
        if app is not None:
            app.terminate()
            app.wait(timeout=20)
        if client is not None:
            client.close()
        if redis is not None:
            redis.terminate()
            redis.wait(timeout=10)
        (output / "result.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
