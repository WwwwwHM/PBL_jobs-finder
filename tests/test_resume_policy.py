"""Versioned six-dimensional resume policy and opt-in service coverage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import get_resume_record
from pbl_jobs_finder.modules.quota import QuotaService
from pbl_jobs_finder.modules.resume_diagnosis import (
    ResumeDiagnosisService,
    ResumeResponseError,
    diagnose_resume,
)
from pbl_jobs_finder.policies.resume_policy import (
    RESUME_GENERAL_POLICY_VERSION,
    ResumePolicyError,
    get_resume_policy,
)


class FakeChatClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system_prompt = ""
        self.user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


def _dimension(score: int, confidence: str = "high") -> dict[str, object]:
    return {
        "score": score,
        "evidence": ["简历中存在可核验的具体事实"],
        "gaps": ["仍缺少一项岗位要求的直接证据"],
        "recommendations": ["补充真实项目中的具体行动和结果"],
        "confidence": confidence,
    }


def _dimensional_response() -> str:
    return json.dumps(
        {
            "dimensions": {
                "hard_skill_match": _dimension(80),
                "experience_relevance": _dimension(70),
                "soft_skill_match": _dimension(60, "medium"),
                "education_match": _dimension(90),
                "keyword_coverage": _dimension(50, "medium"),
                "resume_quality": _dimension(100),
            },
            "strengths": ["Python 后端经历与目标岗位直接相关"],
            "missing_keywords": ["自动化测试"],
            "suggestions": ["补充接口测试方法及其真实结果"],
            "star_examples": [
                "情境/任务：维护订单服务；行动：使用 Python 开发接口；结果：[请补充真实数据]。"
            ],
            "optimized_text": (
                "# 张三\n\n## 项目经历\n- 使用 Python 开发并维护订单服务接口。"
            ),
            "confidence": "medium",
        },
        ensure_ascii=False,
    )


class ResumePolicyTests(unittest.TestCase):
    def test_policy_calculates_weighted_score_and_grade_server_side(self) -> None:
        policy = get_resume_policy()
        payload = policy.parse_response(_dimensional_response())

        self.assertEqual(policy.calculate_score(payload.dimensions), 73)
        self.assertEqual(policy.grade_for(73), "B")
        self.assertEqual(policy.grade_for(75), "A")
        self.assertEqual(policy.grade_for(49), "C")

    def test_dimensional_diagnosis_uses_schema_and_ignores_model_total_score(
        self,
    ) -> None:
        response = json.loads(_dimensional_response())
        response["score"] = 99
        client = FakeChatClient(json.dumps(response, ensure_ascii=False))

        with self.assertRaises(ResumeResponseError):
            diagnose_resume(
                "张三，五年 Python 后端经验，负责订单服务开发与维护。",
                "Python 后端工程师",
                client=client,
                enable_dimensions=True,
            )

        response.pop("score")
        client.response = json.dumps(response, ensure_ascii=False)
        result = diagnose_resume(
            "张三，五年 Python 后端经验，负责订单服务开发与维护。",
            "Python 后端工程师",
            client=client,
            enable_dimensions=True,
        )

        self.assertEqual(result.score, 73)
        self.assertEqual(result.grade, "B")
        self.assertEqual(result.policy_version, RESUME_GENERAL_POLICY_VERSION)
        self.assertIsNotNone(result.dimensions)
        self.assertIn("总分和等级由服务端计算", client.system_prompt)
        self.assertIn("$defs", client.user_prompt)
        self.assertIn("不可信数据", client.system_prompt)

    def test_invalid_dimension_type_and_unknown_policy_are_rejected(self) -> None:
        policy = get_resume_policy()
        response = json.loads(_dimensional_response())
        response["dimensions"]["hard_skill_match"]["score"] = "80"
        with self.assertRaises(ResumePolicyError):
            policy.parse_response(json.dumps(response, ensure_ascii=False))
        with self.assertRaisesRegex(ResumePolicyError, "未知"):
            get_resume_policy("resume-unknown-v1")


class ResumePolicyPersistenceTests(unittest.TestCase):
    def test_opt_in_service_persists_versioned_diagnosis_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = Database(f"sqlite:///{(root / 'test.db').as_posix()}")
            users = {"token": {"phone": "13800138000"}}
            service = ResumeDiagnosisService(
                database_instance=database,
                quota=QuotaService(token_verifier=users.get),
                token_verifier=users.get,
                chat_client=FakeChatClient(_dimensional_response()),
                exports_dir=root / "exports",
                enable_dimensions=True,
                resume_policy_version=RESUME_GENERAL_POLICY_VERSION,
            )
            try:
                outcome = service.diagnose(
                    token="token",
                    position="Python 后端工程师",
                    pasted_text=(
                        "张三，五年 Python 后端经验，负责订单服务开发、测试和维护。"
                    ),
                )
                with database.session() as session:
                    stored = get_resume_record(session, outcome.record_id)
                    metadata = json.loads(stored.diagnosis_json)

                self.assertEqual(stored.score, 73)
                self.assertEqual(stored.grade, "B")
                self.assertEqual(stored.policy_version, RESUME_GENERAL_POLICY_VERSION)
                self.assertEqual(metadata["confidence"], "medium")
                self.assertEqual(
                    metadata["dimensions"]["hard_skill_match"]["score"], 80
                )
            finally:
                database.dispose()


if __name__ == "__main__":
    unittest.main()
