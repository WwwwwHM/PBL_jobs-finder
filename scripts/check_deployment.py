"""Run repeatable deployment preflight checks for the MVP application."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from sqlalchemy import inspect

from pbl_jobs_finder.config import Settings, get_settings
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.modules.question_bank import (
    DEFAULT_QUESTION_BANK_PATH,
    load_question_bank,
)
from pbl_jobs_finder.vector_store import COLLECTION_NAME

EXPECTED_TABLES = {"users", "resume_records", "interview_sessions"}
EXPECTED_QUESTION_COUNT = 42


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One deployment check result safe to print in CI and operator logs."""

    name: str
    passed: bool
    detail: str


def _run_check(name: str, check: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name, True, check())
    except Exception as exc:  # noqa: BLE001 - report every independent preflight failure
        return CheckResult(name, False, f"{type(exc).__name__}: {exc}")


def check_python_version() -> str:
    if sys.version_info < (3, 11) or sys.version_info >= (3, 12):
        raise RuntimeError("requires the tested Python 3.11 runtime")
    return sys.version.split()[0]


def check_api_keys(settings: Settings) -> str:
    missing = []
    if not settings.zhipu_api_key:
        missing.append("ZHIPU_API_KEY")
    if not settings.aliyun_api_key:
        missing.append("ALIYUN_API_KEY")
    if missing:
        raise RuntimeError(f"missing required setting(s): {', '.join(missing)}")
    return "ZHIPU_API_KEY and ALIYUN_API_KEY are configured"


def check_runtime_directories(settings: Settings) -> str:
    settings.ensure_runtime_directories()
    paths = (
        settings.data_dir,
        settings.uploads_dir,
        settings.exports_dir,
        settings.chroma_dir,
        settings.log_dir,
    )
    for path in paths:
        if not path.is_dir():
            raise RuntimeError(f"runtime path is not a directory: {path}")
        with tempfile.NamedTemporaryFile(dir=path, prefix="preflight-", delete=True):
            pass
    return f"{len(paths)} runtime directories are writable"


def check_database(settings: Settings) -> str:
    candidate_database = Database(settings.database_url)
    try:
        candidate_database.initialize()
        tables = set(inspect(candidate_database.engine).get_table_names())
    finally:
        candidate_database.dispose()
    missing = EXPECTED_TABLES - tables
    if missing:
        raise RuntimeError(f"database is missing table(s): {', '.join(sorted(missing))}")
    return f"database initialized with {len(EXPECTED_TABLES)} required tables"


def check_question_source() -> str:
    questions = load_question_bank(DEFAULT_QUESTION_BANK_PATH)
    if len(questions) != EXPECTED_QUESTION_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_QUESTION_COUNT} source questions, found {len(questions)}"
        )
    return f"{len(questions)} structured source questions"


def check_question_index(settings: Settings) -> str:
    client = chromadb.PersistentClient(
        path=str(settings.chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    try:
        collection = client.get_collection(COLLECTION_NAME)
        count = int(collection.count())
    except Exception as exc:
        raise RuntimeError(
            "interview question index is absent; run scripts/import_question_bank.py"
        ) from exc
    if count != EXPECTED_QUESTION_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_QUESTION_COUNT} indexed questions, found {count}"
        )
    return f"{count} indexed interview questions"


def check_chromium() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    if not executable.is_file():
        raise RuntimeError(
            "Chromium is not installed; run `python -m playwright install chromium`"
        )
    return f"Chromium executable found at {executable}"


def check_frontend_build() -> str:
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from frontend import build_app

    app = build_app()
    if app._queue.max_size != 100 or app._queue.default_concurrency_limit != 4:
        raise RuntimeError("frontend queue configuration does not match the MVP baseline")
    return "Gradio application built with queue size 100 and concurrency 4"


def run_preflight(
    *,
    require_api_keys: bool = False,
    require_question_index: bool = False,
    require_chromium: bool = False,
    check_frontend: bool = False,
) -> list[CheckResult]:
    settings = get_settings()
    checks: list[tuple[str, Callable[[], str]]] = [
        ("Python runtime", check_python_version),
        ("Runtime directories", lambda: check_runtime_directories(settings)),
        ("Database", lambda: check_database(settings)),
        ("Question source", check_question_source),
    ]
    if require_api_keys:
        checks.append(("API keys", lambda: check_api_keys(settings)))
    if require_question_index:
        checks.append(("Question index", lambda: check_question_index(settings)))
    if require_chromium:
        checks.append(("Playwright Chromium", check_chromium))
    if check_frontend:
        checks.append(("Frontend build", check_frontend_build))
    return [_run_check(name, check) for name, check in checks]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="require API keys, initialized question index, Chromium, and frontend build",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = run_preflight(
        require_api_keys=args.release,
        require_question_index=args.release,
        require_chromium=args.release,
        check_frontend=args.release,
    )
    for result in results:
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}")
    failures = sum(not result.passed for result in results)
    print(f"Preflight result: {len(results) - failures}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
