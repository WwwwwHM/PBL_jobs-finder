"""Measure real provider latency without printing user or model content."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypeVar

from pbl_jobs_finder.modules.interview_agent import generate_first_question
from pbl_jobs_finder.modules.resume_diagnosis import (
    DIAGNOSIS_MAX_TOKENS,
    diagnose_resume,
)
from pbl_jobs_finder.utils.llm_client import create_default_chat_client
from pbl_jobs_finder.vector_store import create_default_vector_store

RESUME_TARGET_SECONDS = 30.0
INTERVIEW_TARGET_SECONDS = 15.0

SAMPLE_RESUME = (
    "张三，五年 Java 后端开发经验。负责订单服务与支付系统，使用 Spring Boot、"
    "MySQL、Redis 和 Kafka。主导慢查询优化和缓存改造，将接口平均响应时间从 "
    "800 毫秒降至 200 毫秒；设计消息幂等与失败重试机制，参与监控告警和线上"
    "故障复盘。熟悉 Docker、Linux、Git 与单元测试。本科计算机科学与技术专业。"
)
POSITION = "Java 后端工程师"
JOB_DESCRIPTION = "负责微服务设计、数据库性能优化和线上稳定性建设"

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Measurement:
    name: str
    target_seconds: float
    runs: int
    durations_seconds: tuple[float, ...]
    minimum_seconds: float
    median_seconds: float
    mean_seconds: float
    maximum_seconds: float
    passed: bool


def _measure(
    name: str,
    runs: int,
    target_seconds: float,
    operation: Callable[[], T],
) -> Measurement:
    durations = []
    for _ in range(runs):
        started = time.perf_counter()
        operation()
        durations.append(round(time.perf_counter() - started, 3))
    return Measurement(
        name=name,
        target_seconds=target_seconds,
        runs=runs,
        durations_seconds=tuple(durations),
        minimum_seconds=min(durations),
        median_seconds=round(statistics.median(durations), 3),
        mean_seconds=round(statistics.fmean(durations), 3),
        maximum_seconds=max(durations),
        passed=all(duration <= target_seconds for duration in durations),
    )


def run_checks(diagnosis_runs: int, interview_runs: int) -> list[Measurement]:
    diagnosis_client = create_default_chat_client(max_tokens=DIAGNOSIS_MAX_TOKENS)
    interview_client = create_default_chat_client()
    vector_store = create_default_vector_store()

    def diagnose() -> None:
        diagnose_resume(SAMPLE_RESUME, POSITION, client=diagnosis_client)

    def start_interview() -> None:
        references = vector_store.search_for_interview(
            position=POSITION,
            job_description=JOB_DESCRIPTION,
            resume_text=SAMPLE_RESUME,
            top_k=5,
        )
        generate_first_question(
            position=POSITION,
            job_description=JOB_DESCRIPTION,
            resume_text=SAMPLE_RESUME,
            retrieved_questions=references,
            client=interview_client,
        )

    return [
        _measure(
            "resume_diagnosis",
            diagnosis_runs,
            RESUME_TARGET_SECONDS,
            diagnose,
        ),
        _measure(
            "interview_first_round",
            interview_runs,
            INTERVIEW_TARGET_SECONDS,
            start_interview,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis-runs", type=int, default=5)
    parser.add_argument("--interview-runs", type=int, default=10)
    args = parser.parse_args()
    if args.diagnosis_runs <= 0 or args.interview_runs <= 0:
        parser.error("run counts must be greater than zero")

    measurements = run_checks(args.diagnosis_runs, args.interview_runs)
    print(
        json.dumps(
            {
                "measurements": [asdict(item) for item in measurements],
                "passed": all(item.passed for item in measurements),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(item.passed for item in measurements) else 1


if __name__ == "__main__":
    raise SystemExit(main())
