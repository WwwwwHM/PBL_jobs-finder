"""Interview-session creation and first-question generation tests."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import (
    get_interview_session,
    get_recent_interview_sessions,
    update_interview_session,
)
from pbl_jobs_finder.modules.interview_agent import (
    MAX_ANSWER_CHARACTERS,
    InterviewAccessError,
    InterviewService,
    InterviewUnavailableError,
    InterviewValidationError,
    generate_first_question,
)
from pbl_jobs_finder.modules.quota import AuthenticationError, QuotaService
from pbl_jobs_finder.utils.llm_client import LLMServiceError


class FakeVectorStore:
    def __init__(self, questions: list[str] | None = None) -> None:
        self.questions = questions if questions is not None else [
            "如何定位订单服务的性能瓶颈？",
            "如何保证消息消费的幂等性？",
            "请说明 Redis 缓存一致性方案。",
            "如何设计高可用的微服务接口？",
            "如何优化一条执行缓慢的 SQL？",
        ]
        self.calls: list[dict[str, object]] = []

    def search_for_interview(self, **kwargs: object) -> list[str]:
        self.calls.append(kwargs)
        return self.questions


class FakeChatClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.responses: list[str] = []
        self.system_prompt = ""
        self.user_prompt = ""
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        if self.responses:
            return self.responses.pop(0)
        return self.response


class RaisingChatClient:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise LLMServiceError("AI 服务响应超时，请稍后重试")


class ConcurrentAnswerChatClient(FakeChatClient):
    def __init__(self, response: str, delay_seconds: float = 0.1) -> None:
        super().__init__(response)
        self.delay_seconds = delay_seconds
        self.active_calls = 0
        self.max_active_calls = 0
        self._lock = threading.Lock()

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "【候选人回答】" not in user_prompt:
            return super().complete(system_prompt, user_prompt)
        with self._lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(self.delay_seconds)
            return self.response
        finally:
            with self._lock:
                self.active_calls -= 1


class BrokenDatabase:
    def initialize(self) -> None:
        pass

    @contextmanager
    def session(self):
        raise RuntimeError("database unavailable")
        yield


class InterviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(f"sqlite:///{database_path.as_posix()}")
        self.database.initialize()
        self.identities = {
            "token-a": {"phone": "13800138000"},
            "token-b": {"phone": "13900139000"},
        }
        self.quota = QuotaService(
            token_verifier=lambda token: self.identities.get(token)
        )
        self.store = FakeVectorStore()
        self.client = FakeChatClient(
            "问题：请结合你负责的订单系统，说明你会如何定位并解决接口延迟问题？"
        )
        self.service = InterviewService(
            database_instance=self.database,
            quota=self.quota,
            vector_store=self.store,
            chat_client=self.client,
            token_verifier=lambda token: self.identities.get(token),
        )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def _report_json(**score_overrides: object) -> str:
        scores = {"logic": 82, "professional": 78, "communication": 85}
        scores.update(score_overrides)
        return json.dumps(
            {
                "scores": scores,
                "summary": "候选人能够按步骤分析问题并说明主要取舍，整体表达清楚，但部分技术细节和量化验证仍需加强。",
                "knowledge_gaps": [
                    "缺少对容量指标和告警阈值的具体说明",
                    "故障恢复目标与数据一致性取舍阐述不足",
                ],
                "improvement_suggestions": [
                    "回答系统设计题时先明确约束，再说明方案、取舍和验证指标",
                    "为项目案例补充延迟、吞吐量或恢复时间等真实量化结果",
                ],
                "reference_answers": [
                    {
                        "question": "请总结你解决复杂线上故障的方法论和复盘方式？",
                        "answer": "先确认影响范围并止损，再按监控、日志和链路追踪收集证据，定位根因后灰度修复，最后用复盘行动项验证改进是否生效。",
                    }
                ],
            },
            ensure_ascii=False,
        )

    def _move_to_final_question(self, session_id: int) -> str:
        final_question = "请总结你解决复杂线上故障的方法论和复盘方式？"
        with self.database.session() as session:
            saved = get_interview_session(session, session_id)
            conversation = json.loads(saved.conversation_json)
            conversation.append(
                {
                    "role": "interviewer",
                    "content": final_question,
                    "kind": "main_question",
                    "round": 5,
                }
            )
            update_interview_session(
                session,
                session_id,
                current_question=final_question,
                question_rounds=5,
                follow_up_count=0,
                conversation=conversation,
            )
        return final_question

    def test_start_uses_all_context_and_persists_first_question_state(self) -> None:
        outcome = self.service.start(
            token="token-a",
            position=" Java 后端开发工程师 ",
            job_description="负责 Spring Boot 微服务和 MySQL 优化",
            resume_text="负责订单系统，使用 Redis 和 Kafka",
        )

        self.assertEqual(outcome.question_round, 1)
        self.assertEqual(outcome.total_questions, 5)
        self.assertNotIn("问题：", outcome.question)
        self.assertEqual(self.quota.status("token-a").used, 1)
        self.assertEqual(len(self.store.calls), 1)
        retrieval = self.store.calls[0]
        self.assertEqual(retrieval["position"], "Java 后端开发工程师")
        self.assertEqual(retrieval["top_k"], 5)
        self.assertIn("Spring Boot", self.client.user_prompt)
        self.assertIn("订单系统", self.client.user_prompt)
        self.assertIn("性能瓶颈", self.client.user_prompt)
        self.assertIn("不可信资料", self.client.system_prompt)

        with self.database.session() as session:
            saved = get_interview_session(session, outcome.session_id)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.phone, "13800138000")
            self.assertEqual(saved.status, "in_progress")
            self.assertEqual(saved.question_rounds, 1)
            self.assertEqual(saved.current_question, outcome.question)
            conversation = json.loads(saved.conversation_json)
            self.assertEqual(conversation[0]["kind"], "main_question")
            self.assertEqual(conversation[0]["round"], 1)

    def test_optional_context_and_sessions_are_isolated_by_user(self) -> None:
        first = self.service.start(token="token-a", position="产品经理")
        second = self.service.start(token="token-b", position="产品经理")

        self.assertNotEqual(first.session_id, second.session_id)
        self.assertIn("（未提供）", self.client.user_prompt)
        with self.database.session() as session:
            user_a = get_recent_interview_sessions(session, "13800138000")
            user_b = get_recent_interview_sessions(session, "13900139000")
            self.assertEqual([item.id for item in user_a], [first.session_id])
            self.assertEqual([item.id for item in user_b], [second.session_id])

    def test_interview_modes_are_flag_gated_before_quota_and_retrieval(self) -> None:
        with self.assertRaisesRegex(InterviewValidationError, "尚未启用"):
            self.service.start(
                token="token-a",
                position="Java 后端工程师",
                mode="focused_live",
            )
        self.assertEqual(self.quota.status("token-a").used, 0)
        self.assertEqual(self.store.calls, [])

    def test_focused_deferred_mode_uses_persisted_plan_and_finishes_at_three(
        self,
    ) -> None:
        service = InterviewService(
            database_instance=self.database,
            quota=self.quota,
            vector_store=self.store,
            chat_client=self.client,
            token_verifier=lambda token: self.identities.get(token),
            enable_modes=True,
        )
        started = service.start(
            token="token-a",
            position="Java 后端工程师",
            mode="focused_live",
            feedback_mode="deferred",
        )

        self.assertEqual(started.total_questions, 3)
        self.assertEqual(started.max_follow_up_count, 1)
        self.assertEqual(started.feedback_mode, "deferred")
        self.assertEqual(self.store.calls[0]["top_k"], 3)
        self.assertIn("核验最相关经历", self.client.user_prompt)
        with self.database.session() as session:
            saved = get_interview_session(session, started.session_id)
            plan = json.loads(saved.question_plan_json)
            self.assertEqual(saved.mode, "focused_live")
            self.assertEqual(saved.feedback_mode, "deferred")
            self.assertEqual(len(plan), 3)

            final_question = "请说明你会如何复盘一次关键技术决策并推动改进？"
            conversation = json.loads(saved.conversation_json)
            conversation.append(
                {
                    "role": "interviewer",
                    "content": final_question,
                    "kind": "main_question",
                    "round": 3,
                }
            )
            update_interview_session(
                session,
                started.session_id,
                current_question=final_question,
                question_rounds=3,
                follow_up_count=0,
                conversation=conversation,
            )

        self.client.responses = [
            json.dumps(
                {
                    "feedback": "回答给出了复盘步骤和改进行动，但还可以补充如何验证行动项已经产生效果。",
                    "needs_follow_up": False,
                    "next_question": "模型不应保留的额外问题？",
                },
                ensure_ascii=False,
            ),
            self._report_json(),
        ]
        completed = service.submit_answer(
            token="token-a",
            session_id=started.session_id,
            answer="我会整理事实和决策依据，明确行动项、负责人和验证指标。",
            expected_question=final_question,
        )

        self.assertTrue(completed.is_finished)
        self.assertEqual(completed.total_questions, 3)
        self.assertEqual(completed.max_follow_up_count, 1)
        self.assertEqual(completed.feedback_mode, "deferred")
        self.assertEqual(completed.feedback, "")
        self.assertIsNotNone(completed.report)

    def test_question_prompts_forbid_cross_section_technology_attribution(self) -> None:
        resume = """项目经历
AI 求职助手：使用 Gradio、ChromaDB 和 GLM 实现简历诊断与模拟面试。

主修课程
模式识别、数字信号处理。"""
        started = self.service.start(
            token="token-a",
            position="AI 应用开发工程师",
            resume_text=resume,
        )

        self.assertIn(
            "不得把技能清单、课程、研究方向或其他项目中的技术",
            self.client.system_prompt,
        )
        self.assertIn("不代表候选人使用过相关技术", self.client.user_prompt)
        self.assertIn("模式识别、数字信号处理", self.client.user_prompt)

        self.client.response = json.dumps(
            {
                "feedback": "回答说明了项目目标和主要组件，建议进一步补充检索效果的验证指标。",
                "needs_follow_up": False,
                "next_question": "在这个项目中，你如何评估 ChromaDB 的检索质量并调整召回策略？",
            },
            ensure_ascii=False,
        )
        self.service.submit_answer(
            token="token-a",
            session_id=started.session_id,
            answer="我用命中率和人工评审样本检查召回结果，并调整查询文本。",
            expected_question=started.question,
        )

        self.assertIn(
            "不得把技能清单、课程、研究方向或其他项目中的技术",
            self.client.system_prompt,
        )
        self.assertIn("不代表候选人使用过相关技术", self.client.user_prompt)

    def test_invalid_inputs_are_rejected_before_quota_and_retrieval(self) -> None:
        invalid_cases = [
            {"position": " "},
            {"position": "P" * 101},
            {"position": "后端工程师", "job_description": "J" * 6001},
            {"position": "后端工程师", "resume_text": "R" * 6001},
        ]
        for values in invalid_cases:
            with (
                self.subTest(values=list(values)),
                self.assertRaises(InterviewValidationError),
            ):
                self.service.start(token="token-a", **values)

        self.assertEqual(self.quota.status("token-a").used, 0)
        self.assertEqual(self.store.calls, [])

    def test_answer_feedback_and_next_main_question_are_persisted_without_quota(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        self.client.response = json.dumps(
            {
                "feedback": "回答给出了监控和链路追踪思路，但还可以补充如何用指标定位具体瓶颈。建议说明判断顺序和验证方法。",
                "needs_follow_up": False,
                "next_question": "请说明你会如何设计消息消费的幂等机制，并处理重复消息？",
            },
            ensure_ascii=False,
        )
        outcome = self.service.submit_answer(
            token="token-a",
            session_id=started.session_id,
            answer=" 我先通过监控定位慢调用，再结合日志和链路追踪确认瓶颈。 ",
            expected_question=started.question,
        )

        self.assertEqual(outcome.session_id, started.session_id)
        self.assertEqual(outcome.question_round, 2)
        self.assertFalse(outcome.is_follow_up)
        self.assertFalse(outcome.is_finished)
        self.assertIn("幂等", outcome.next_question)
        self.assertEqual(self.quota.status("token-a").used, 1)
        with self.database.session() as session:
            saved = get_interview_session(session, started.session_id)
            conversation = json.loads(saved.conversation_json)
            self.assertEqual(
                [item["kind"] for item in conversation],
                ["main_question", "answer", "feedback", "main_question"],
            )
            self.assertEqual(saved.question_rounds, 2)
            self.assertEqual(saved.current_question, outcome.next_question)

        with self.assertRaisesRegex(InterviewValidationError, "问题已更新"):
            self.service.submit_answer(
                token="token-a",
                session_id=started.session_id,
                answer="重复回答",
                expected_question=started.question,
            )

    def test_different_interview_answers_can_run_concurrently(self) -> None:
        first = self.service.start(token="token-a", position="Java 后端工程师")
        second = self.service.start(token="token-b", position="Java 后端工程师")
        response = json.dumps(
            {
                "feedback": "回答说明了定位步骤，建议继续补充验证指标和回滚方案。",
                "needs_follow_up": False,
                "next_question": "请说明你会如何保证消息消费的幂等性？",
            },
            ensure_ascii=False,
        )
        client = ConcurrentAnswerChatClient(response)
        self.service.chat_client = client

        def submit(token: str, session_id: int, question: str) -> None:
            self.service.submit_answer(
                token=token,
                session_id=session_id,
                answer="我会先收集指标，再定位根因并验证修复效果。",
                expected_question=question,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(submit, "token-a", first.session_id, first.question),
                executor.submit(submit, "token-b", second.session_id, second.question),
            ]
            for future in futures:
                future.result()

        self.assertEqual(client.max_active_calls, 2)

    def test_duplicate_answers_for_one_session_are_serialized(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        response = json.dumps(
            {
                "feedback": "回答说明了定位步骤，建议继续补充验证指标和回滚方案。",
                "needs_follow_up": False,
                "next_question": "请说明你会如何保证消息消费的幂等性？",
            },
            ensure_ascii=False,
        )
        client = ConcurrentAnswerChatClient(response)
        self.service.chat_client = client

        def submit() -> object:
            try:
                return self.service.submit_answer(
                    token="token-a",
                    session_id=started.session_id,
                    answer="我会先收集指标，再定位根因并验证修复效果。",
                    expected_question=started.question,
                )
            except InterviewValidationError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        failures = [item for item in results if isinstance(item, Exception)]
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], InterviewValidationError)
        self.assertEqual(client.max_active_calls, 1)

    def test_follow_ups_stay_on_round_and_fourth_answer_forces_next_main(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        current_question = started.question
        for expected_count in range(1, 4):
            self.client.response = json.dumps(
                {
                    "feedback": "回答方向相关，但缺少实现细节和验证结果。请补充一个具体步骤。",
                    "needs_follow_up": True,
                    "next_question": f"请补充第 {expected_count} 个具体实现细节和验证方式？",
                },
                ensure_ascii=False,
            )
            outcome = self.service.submit_answer(
                token="token-a",
                session_id=started.session_id,
                answer=f"这是第 {expected_count} 次补充回答，包含一些相关细节。",
                expected_question=current_question,
            )
            self.assertTrue(outcome.is_follow_up)
            self.assertEqual(outcome.question_round, 1)
            self.assertEqual(outcome.follow_up_count, expected_count)
            current_question = outcome.next_question

        self.client.response = json.dumps(
            {
                "feedback": "本题已完成三次追问，现有回答足以识别主要思路。后续应继续强化量化验证。",
                "needs_follow_up": True,
                "next_question": "请设计一个高并发库存扣减方案并说明一致性取舍？",
            },
            ensure_ascii=False,
        )
        advanced = self.service.submit_answer(
            token="token-a",
            session_id=started.session_id,
            answer="第四次回答补充了量化验证。",
            expected_question=current_question,
        )
        self.assertFalse(advanced.is_follow_up)
        self.assertEqual(advanced.question_round, 2)
        self.assertEqual(advanced.follow_up_count, 0)

    def test_fifth_main_question_completion_marks_session_completed(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        final_question = self._move_to_final_question(started.session_id)
        self.client.responses = [
            json.dumps(
                {
                    "feedback": "回答覆盖了止损、定位、修复和复盘环节，并说明了验证闭环。建议进一步量化恢复时间目标。",
                    "needs_follow_up": False,
                    "next_question": "模型不应保留的额外问题？",
                },
                ensure_ascii=False,
            ),
            self._report_json(),
        ]
        outcome = self.service.submit_answer(
            token="token-a",
            session_id=started.session_id,
            answer="我会依次止损、收集证据、定位根因、灰度修复并完成复盘。",
            expected_question=final_question,
        )
        self.assertTrue(outcome.is_finished)
        self.assertEqual(outcome.next_question, "")
        self.assertIsNotNone(outcome.report)
        self.assertEqual(outcome.report.logic_score, 82)
        with self.database.session() as session:
            saved = get_interview_session(session, started.session_id)
            self.assertEqual(saved.status, "completed")
            self.assertEqual(saved.current_question, "")
            self.assertEqual(json.loads(saved.report)["scores"]["professional"], 78)

        loaded = self.service.get_report(
            token="token-a", session_id=started.session_id
        )
        self.assertEqual(loaded.communication_score, 85)
        with self.assertRaises(InterviewAccessError):
            self.service.get_report(token="token-b", session_id=started.session_id)

    def test_invalid_report_leaves_final_answer_uncommitted_for_retry(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        final_question = self._move_to_final_question(started.session_id)
        self.client.responses = [
            json.dumps(
                {
                    "feedback": "回答覆盖了主要处置步骤，但仍需补充恢复目标和验证指标。",
                    "needs_follow_up": False,
                    "next_question": "",
                },
                ensure_ascii=False,
            ),
            self._report_json(logic=101),
        ]

        with self.assertRaisesRegex(InterviewValidationError, "评分超出"):
            self.service.submit_answer(
                token="token-a",
                session_id=started.session_id,
                answer="我会先止损，再定位根因、灰度修复并组织复盘。",
                expected_question=final_question,
            )

        with self.database.session() as session:
            saved = get_interview_session(session, started.session_id)
            self.assertEqual(saved.status, "in_progress")
            self.assertEqual(saved.current_question, final_question)
            self.assertEqual(saved.report, "")
            conversation = json.loads(saved.conversation_json)
            self.assertNotEqual(conversation[-1].get("kind"), "answer")

    def test_answer_requires_valid_input_and_session_ownership(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        with self.assertRaises(InterviewAccessError):
            self.service.submit_answer(
                token="token-b",
                session_id=started.session_id,
                answer="另一个用户的回答",
                expected_question=started.question,
            )
        with self.assertRaises(AuthenticationError):
            self.service.submit_answer(
                token="invalid",
                session_id=started.session_id,
                answer="回答",
                expected_question=started.question,
            )
        for answer in [" ", "A" * (MAX_ANSWER_CHARACTERS + 1)]:
            with (
                self.subTest(answer_length=len(answer)),
                self.assertRaises(InterviewValidationError),
            ):
                self.service.submit_answer(
                    token="token-a",
                    session_id=started.session_id,
                    answer=answer,
                    expected_question=started.question,
                )
        with self.assertRaisesRegex(InterviewValidationError, "先开始"):
            self.service.submit_answer(
                token="token-a",
                session_id=None,
                answer="有效回答",
                expected_question=started.question,
            )

    def test_answer_model_failures_leave_session_unchanged(self) -> None:
        started = self.service.start(token="token-a", position="Java 后端工程师")
        original_usage = self.quota.status("token-a").used

        failing_clients = [
            (RaisingChatClient(), LLMServiceError),
            (FakeChatClient("不是 JSON"), InterviewValidationError),
        ]
        for client, expected_error in failing_clients:
            service = InterviewService(
                database_instance=self.database,
                quota=self.quota,
                vector_store=self.store,
                chat_client=client,
                token_verifier=lambda token: self.identities.get(token),
            )
            with (
                self.subTest(client=type(client).__name__),
                self.assertRaises(expected_error),
            ):
                service.submit_answer(
                    token="token-a",
                    session_id=started.session_id,
                    answer="我会先查看监控指标，再结合链路追踪定位瓶颈。",
                    expected_question=started.question,
                )

            with self.database.session() as session:
                saved = get_interview_session(session, started.session_id)
                self.assertEqual(saved.status, "in_progress")
                self.assertEqual(saved.question_rounds, 1)
                self.assertEqual(saved.follow_up_count, 0)
                self.assertEqual(saved.current_question, started.question)
                self.assertEqual(len(json.loads(saved.conversation_json)), 1)
            self.assertEqual(self.quota.status("token-a").used, original_usage)

    def test_authentication_empty_bank_and_model_failures_do_not_create_sessions(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.service.start(token="invalid", position="后端工程师")

        empty_service = InterviewService(
            database_instance=self.database,
            quota=self.quota,
            vector_store=FakeVectorStore([]),
            chat_client=self.client,
        )
        with self.assertRaises(InterviewUnavailableError):
            empty_service.start(token="token-a", position="后端工程师")
        self.assertEqual(self.client.calls, 0)
        self.assertEqual(self.quota.status("token-a").used, 0)

        failing_service = InterviewService(
            database_instance=self.database,
            quota=self.quota,
            vector_store=self.store,
            chat_client=RaisingChatClient(),
        )
        with self.assertRaises(LLMServiceError):
            failing_service.start(token="token-a", position="后端工程师")
        self.assertEqual(self.quota.status("token-a").used, 0)
        with self.database.session() as session:
            self.assertEqual(get_recent_interview_sessions(session, "13800138000"), [])

    def test_database_failure_rolls_back_quota(self) -> None:
        service = InterviewService(
            database_instance=BrokenDatabase(),
            quota=self.quota,
            vector_store=self.store,
            chat_client=self.client,
        )
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            service.start(token="token-a", position="后端工程师")
        self.assertEqual(self.quota.status("token-a").used, 0)

    def test_generated_question_validation_rejects_empty_long_and_unsafe_output(self) -> None:
        cases = ["", "太短", "Q" * 501, "```markdown\n问题\n```", "<script>alert(1)</script>"]
        for response in cases:
            with (
                self.subTest(response=response[:20]),
                self.assertRaises(InterviewValidationError),
            ):
                generate_first_question(
                    position="后端工程师",
                    job_description="",
                    resume_text="",
                    retrieved_questions=["如何设计可靠接口？"],
                    client=FakeChatClient(response),
                )


if __name__ == "__main__":
    unittest.main()
