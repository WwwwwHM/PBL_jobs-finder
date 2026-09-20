"""Run the final live-service acceptance path against isolated business data."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.modules.auth import AuthService
from pbl_jobs_finder.modules.history import HistoryService
from pbl_jobs_finder.modules.interview_agent import InterviewService
from pbl_jobs_finder.modules.quota import QuotaService
from pbl_jobs_finder.modules.resume_diagnosis import ResumeDiagnosisService
from pbl_jobs_finder.vector_store import create_default_vector_store

PHONE = "13900000023"
POSITION = "Java 后端工程师"
JOB_DESCRIPTION = "负责微服务设计、数据库性能优化、消息可靠性和线上稳定性建设。"
SAMPLE_RESUME = (
    "张三，五年 Java 后端开发经验，本科计算机科学与技术专业。"
    "负责订单服务与支付系统，使用 Spring Boot、MySQL、Redis 和 Kafka。"
    "主导慢查询优化和缓存改造，将接口平均响应时间从 800 毫秒降至 200 毫秒；"
    "设计消息幂等与失败重试机制，参与监控告警和线上故障复盘。"
    "熟悉 Docker、Linux、Git 与单元测试。"
)
SUPPLEMENT = (
    "曾为订单服务补充压测方案，并依据容量测试结果调整连接池和告警阈值。"
)
ANSWER = (
    "我会先明确目标、影响范围和成功指标，再按监控、日志、链路追踪和数据库指标"
    "逐层定位。确认瓶颈后提出至少两个方案，比较一致性、性能、复杂度和回滚成本；"
    "在测试环境复现并压测，通过灰度发布验证关键指标，保留回滚开关。上线后持续"
    "观察延迟、错误率和资源使用率，并把根因、取舍和预防措施写入复盘。"
)
CODE_PATTERN = re.compile(r"verification code for \d{11}: (\d{6})")
EXPECTED_QUESTION_COUNT = 42


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    diagnosis_seconds: float
    pdf_seconds: float
    interview_seconds: float
    answer_submissions: int
    resume_history_items: int
    interview_history_items: int
    quota_used: int
    passed: bool


def _measure(operation):
    started = time.perf_counter()
    result = operation()
    return result, round(time.perf_counter() - started, 3)


def _login(auth: AuthService) -> str:
    console = io.StringIO()
    with redirect_stdout(console):
        sent = auth.send_verification_code(PHONE)
    if not sent.success:
        raise RuntimeError("verification code request failed")
    match = CODE_PATTERN.search(console.getvalue())
    if match is None:
        raise RuntimeError("mock verification code was not written to the console")
    login = auth.verify_login(PHONE, match.group(1))
    if not login.success or not login.data:
        raise RuntimeError("verification-code login failed")
    return login.data


def run_acceptance(question_index: Path) -> AcceptanceResult:
    settings = get_settings()
    if not settings.zhipu_api_key or not settings.aliyun_api_key:
        raise RuntimeError("ZHIPU_API_KEY and ALIYUN_API_KEY must be configured")
    if not question_index.is_dir():
        raise RuntimeError("question index is absent; run the question-bank import")
    browser_cache = settings.project_root / ".playwright-browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and browser_cache.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)

    with TemporaryDirectory(prefix="pbl-final-acceptance-") as runtime:
        runtime_path = Path(runtime)
        database = Database(
            f"sqlite:///{(runtime_path / 'acceptance.db').as_posix()}"
        )
        auth = AuthService(database)
        quota = QuotaService(
            daily_limit=settings.daily_quota,
            timezone_name=settings.timezone,
            token_verifier=auth.verify_token,
        )
        resume_service = ResumeDiagnosisService(
            database_instance=database,
            quota=quota,
            token_verifier=auth.verify_token,
            exports_dir=runtime_path / "exports",
        )
        vector_store = create_default_vector_store(persist_directory=question_index)
        if vector_store.count != EXPECTED_QUESTION_COUNT:
            raise RuntimeError(
                f"expected {EXPECTED_QUESTION_COUNT} indexed questions, "
                f"found {vector_store.count}"
            )
        interview_service = InterviewService(
            database_instance=database,
            quota=quota,
            vector_store=vector_store,
            token_verifier=auth.verify_token,
        )
        history_service = HistoryService(
            database,
            token_verifier=auth.verify_token,
            timezone_name=settings.timezone,
        )

        try:
            token = _login(auth)
            if quota.status(token).remaining != settings.daily_quota:
                raise RuntimeError("new login did not receive the full daily quota")

            diagnosis, diagnosis_seconds = _measure(
                lambda: resume_service.diagnose(
                    token=token,
                    position=POSITION,
                    pasted_text=SAMPLE_RESUME,
                )
            )
            if not 0 <= diagnosis.diagnosis.score <= 100:
                raise RuntimeError("diagnosis score is outside the accepted range")

            generated, pdf_seconds = _measure(
                lambda: resume_service.generate_pdf_resume(
                    token=token,
                    record_id=diagnosis.record_id,
                    optimized_text=diagnosis.diagnosis.optimized_text,
                    supplemental_experience=SUPPLEMENT,
                )
            )
            if (
                not generated.pdf_path.is_file()
                or generated.pdf_path.stat().st_size < 1_000
                or generated.pdf_path.read_bytes()[:4] != b"%PDF"
            ):
                raise RuntimeError("generated resume is not a readable PDF artifact")

            interview_started = time.perf_counter()
            interview = interview_service.start(
                token=token,
                position=POSITION,
                job_description=JOB_DESCRIPTION,
                resume_text=diagnosis.diagnosis.optimized_text,
            )
            question = interview.question
            answer_submissions = 0
            while answer_submissions < 20:
                outcome = interview_service.submit_answer(
                    token=token,
                    session_id=interview.session_id,
                    answer=ANSWER,
                    expected_question=question,
                )
                answer_submissions += 1
                if outcome.is_finished:
                    break
                question = outcome.next_question
            else:
                raise RuntimeError("interview did not finish within its documented limit")
            interview_seconds = round(time.perf_counter() - interview_started, 3)

            report = interview_service.get_report(
                token=token,
                session_id=interview.session_id,
            )
            if not all(
                0 <= score <= 100
                for score in (
                    report.logic_score,
                    report.professional_score,
                    report.communication_score,
                )
            ):
                raise RuntimeError("interview report score is outside the accepted range")

            history = history_service.get_recent(token)
            if len(history.resumes) != 1 or len(history.interviews) != 1:
                raise RuntimeError("final records are missing from user history")
            if history.interviews[0].status != "completed":
                raise RuntimeError("completed interview has the wrong history status")

            quota_used = quota.status(token).used
            if quota_used != 2:
                raise RuntimeError("diagnosis and interview did not consume exactly two uses")
            if not auth.revoke_token(token) or auth.verify_token(token) is not None:
                raise RuntimeError("logout did not revoke the active token")

            return AcceptanceResult(
                diagnosis_seconds=diagnosis_seconds,
                pdf_seconds=pdf_seconds,
                interview_seconds=interview_seconds,
                answer_submissions=answer_submissions,
                resume_history_items=len(history.resumes),
                interview_history_items=len(history.interviews),
                quota_used=quota_used,
                passed=True,
            )
        finally:
            database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live-services",
        action="store_true",
        help="acknowledge that the run calls paid external AI services",
    )
    parser.add_argument(
        "--question-index",
        type=Path,
        default=None,
        help="existing Chroma question index (defaults to CHROMA_DIR)",
    )
    args = parser.parse_args()
    if not args.confirm_live_services:
        parser.error("--confirm-live-services is required")

    settings = get_settings()
    result = run_acceptance((args.question_index or settings.chroma_dir).resolve())
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
