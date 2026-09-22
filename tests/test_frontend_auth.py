"""Frontend callback contracts for session restoration and logout."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import frontend
from pbl_jobs_finder import Message
from pbl_jobs_finder.modules.history import (
    HistorySnapshot,
    InterviewHistoryDetail,
    InterviewHistoryItem,
    ResumeHistoryDetail,
    ResumeHistoryItem,
)
from pbl_jobs_finder.modules.interview_agent import (
    InterviewAnswerOutcome,
    InterviewReport,
    InterviewStartOutcome,
    InterviewValidationError,
    ReferenceAnswer,
)
from pbl_jobs_finder.modules.quota import AuthenticationError, QuotaStatus
from pbl_jobs_finder.modules.resume_diagnosis import (
    DiagnosisOutcome,
    GeneratedResumeOutcome,
    ResumeDiagnosis,
)
from pbl_jobs_finder.modules.resume_pdf import (
    ResumeBasics,
    ResumeDocument,
    ResumePDFError,
)


class FrontendAuthTests(unittest.TestCase):
    def test_login_stores_verified_token_in_state_and_browser_bridge(self) -> None:
        with (
            patch.object(
                frontend,
                "verify_login",
                return_value=Message.success("登录成功", data="token"),
            ),
            patch.object(
                frontend,
                "verify_token",
                return_value={
                    "phone": "13800138000",
                    "nickname": "求职者",
                },
            ),
            patch.object(
                frontend.quota_service,
                "status",
                return_value=QuotaStatus(10, 2, date(2026, 9, 14)),
            ),
        ):
            result = frontend.login("13800138000", "123456")
        self.assertFalse(result[0]["visible"])
        self.assertTrue(result[1]["visible"])
        self.assertIn("13800138000", result[3])
        self.assertEqual(result[4], "今日剩余 8 / 10 次")
        self.assertEqual(result[5:], ("token", "token"))

    def test_invalid_browser_token_clears_state(self) -> None:
        with patch.object(frontend, "verify_token", return_value=None):
            result = frontend.restore_login("forged-token")
        self.assertTrue(result[0]["visible"])
        self.assertFalse(result[1]["visible"])
        self.assertIn("登录已失效", result[2])
        self.assertEqual(result[3:], ("", "", "", ""))

    def test_failed_login_stays_logged_out(self) -> None:
        with patch.object(
            frontend,
            "verify_login",
            return_value=Message.failure("验证码错误"),
        ):
            result = frontend.login("13800138000", "000000")
        self.assertEqual(result[2], "验证码错误")
        self.assertEqual(result[5:], ("", ""))

    def test_logout_revokes_token_and_clears_credentials(self) -> None:
        with patch.object(frontend, "revoke_token") as revoke:
            result = frontend.logout("token")
        revoke.assert_called_once_with("token")
        self.assertTrue(result[0]["visible"])
        self.assertFalse(result[1]["visible"])
        self.assertEqual(result[2], "已退出登录")
        self.assertEqual(result[3:], ("", "", "", "", "", ""))

    def test_token_revoked_during_restore_returns_login_view(self) -> None:
        with (
            patch.object(
                frontend,
                "verify_token",
                return_value={
                    "phone": "13800138000",
                    "nickname": "求职者",
                },
            ),
            patch.object(
                frontend.quota_service, "status", side_effect=AuthenticationError
            ),
        ):
            result = frontend.restore_login("token")
        self.assertEqual(result[5:], ("", ""))

    def test_app_builds_with_login_restore_and_logout_events(self) -> None:
        app = frontend.build_app()
        callbacks = {fn.fn for fn in app.fns.values() if fn.fn is not None}
        self.assertTrue(
            {
                frontend.diagnose_resume_callback,
                frontend.generate_resume_callback,
                frontend.start_interview_callback,
                frontend.submit_interview_answer_callback,
                frontend.clear_interview_workspace,
                frontend.clear_history_workspace,
                frontend.load_history_callback,
                frontend.load_interview_history_detail_callback,
                frontend.load_resume_history_detail_callback,
                frontend.use_optimized_resume_callback,
                frontend.open_supplement_callback,
                frontend.login,
                frontend.restore_login,
                frontend.logout,
            }
            <= callbacks
        )
        self.assertEqual(app._queue.max_size, 100)
        self.assertEqual(app._queue.default_concurrency_limit, 4)
        limits = {
            fn.fn: fn.concurrency_limit
            for fn in app.fns.values()
            if fn.fn
            in {
                frontend.start_interview_callback,
                frontend.submit_interview_answer_callback,
            }
        }
        self.assertEqual(limits[frontend.start_interview_callback], 4)
        self.assertEqual(limits[frontend.submit_interview_answer_callback], 4)

    def test_history_callback_formats_recent_summaries(self) -> None:
        snapshot = HistorySnapshot(
            resumes=(
                ResumeHistoryItem(12, "2026-09-18 15:20", "Python 后端工程师", 88),
            ),
            interviews=(
                InterviewHistoryItem(
                    23,
                    "2026-09-18 16:10",
                    "Java 后端工程师",
                    5,
                    "completed",
                ),
            ),
        )
        with patch.object(frontend.history_service, "get_recent", return_value=snapshot):
            resumes, interviews, status, detail = frontend.load_history_callback("token")

        self.assertEqual(
            resumes[0], [12, "2026-09-18 15:20", "Python 后端工程师", "88 / 100"]
        )
        self.assertEqual(
            interviews[0],
            [23, "2026-09-18 16:10", "Java 后端工程师", "5 轮", "已完成"],
        )
        self.assertIn("简历诊断 1 条", status)
        self.assertIn("模拟面试 1 条", status)
        self.assertEqual(detail, "")

    def test_history_callback_handles_empty_invalid_and_unexpected_failures(self) -> None:
        with patch.object(
            frontend.history_service,
            "get_recent",
            return_value=HistorySnapshot(resumes=(), interviews=()),
        ):
            empty = frontend.load_history_callback("token")
        self.assertEqual(empty[:2], ([], []))
        self.assertIn("暂无历史记录", empty[2])

        with patch.object(
            frontend.history_service,
            "get_recent",
            side_effect=AuthenticationError("登录已失效，请重新登录"),
        ):
            invalid = frontend.load_history_callback("expired-token")
        self.assertEqual(invalid[:2], ([], []))
        self.assertIn("登录已失效", invalid[2])

        with (
            patch.object(
                frontend.history_service,
                "get_recent",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch.object(frontend, "report_exception", return_value="history123"),
        ):
            failed = frontend.load_history_callback("token")
        self.assertIn("history123", failed[2])

    def test_history_selection_formats_owned_resume_and_interview_details(self) -> None:
        resume_detail = ResumeHistoryDetail(
            record_id=12,
            created_at="2026-09-18 15:20",
            target_position="Python 后端工程师",
            score=88,
            grade="A",
            policy_version="resume-general-v1",
            template_id="technical",
            missing_keywords=("容量规划",),
            suggestions="补充真实指标",
            optimized_text="<script>alert('x')</script>\n# 优化稿",
            diagnosis={
                "strengths": ["项目职责清晰"],
                "dimensions": {
                    "hard_skill_match": {
                        "score": 86,
                        "confidence": "high",
                        "evidence": ["项目使用 Python"],
                        "gaps": [],
                        "recommendations": ["补充性能数据"],
                    }
                },
            },
        )
        interview_detail = InterviewHistoryDetail(
            session_id=23,
            created_at="2026-09-18 16:10",
            position="Java 后端工程师",
            status="completed",
            question_rounds=3,
            mode="focused_live",
            feedback_mode="deferred",
            policy_version="interview-standard-v1",
            conversation=(
                {
                    "role": "interviewer",
                    "kind": "main_question",
                    "round": 1,
                    "content": "请介绍代表项目",
                },
                {
                    "role": "candidate",
                    "kind": "answer",
                    "round": 1,
                    "content": "我负责接口设计",
                },
            ),
            report={
                "scores": {"logic": 84, "professional": 81, "communication": 87},
                "summary": "回答结构清晰",
                "knowledge_gaps": ["容量规划"],
                "improvement_suggestions": ["补充验证指标"],
                "reference_answers": [],
            },
        )
        resume_event = type(
            "Selection", (), {"selected": True, "row_value": [12, "time"]}
        )()
        interview_event = type(
            "Selection", (), {"selected": True, "row_value": [23, "time"]}
        )()
        with (
            patch.object(
                frontend.history_service,
                "get_resume_detail",
                return_value=resume_detail,
            ) as get_resume,
            patch.object(
                frontend.history_service,
                "get_interview_detail",
                return_value=interview_detail,
            ) as get_interview,
        ):
            resume_output = frontend.load_resume_history_detail_callback(
                "token", resume_event
            )
            interview_output = frontend.load_interview_history_detail_callback(
                "token", interview_event
            )

        get_resume.assert_called_once_with("token", 12)
        get_interview.assert_called_once_with("token", 23)
        self.assertIn("硬技能匹配度", resume_output)
        self.assertIn("技术重点", resume_output)
        self.assertNotIn("<script>", resume_output)
        self.assertIn("快速面试", interview_output)
        self.assertIn("面试后反馈", interview_output)
        self.assertIn("我负责接口设计", interview_output)
        self.assertIn("84 / 100", interview_output)

    def test_interview_callback_displays_first_question_and_updates_quota(self) -> None:
        outcome = InterviewStartOutcome(
            session_id=23,
            question="请结合订单系统说明你如何定位接口延迟问题？",
        )
        with (
            patch.object(frontend.interview_service, "start", return_value=outcome) as start,
            patch.object(
                frontend.quota_service,
                "status",
                return_value=QuotaStatus(10, 3, date(2026, 9, 17)),
            ),
        ):
            result = frontend.start_interview_callback(
                "Java 后端开发工程师",
                "负责 Spring Boot 微服务",
                "负责订单系统开发",
                "token",
            )

        start.assert_called_once_with(
            token="token",
            position="Java 后端开发工程师",
            job_description="负责 Spring Boot 微服务",
            resume_text="负责订单系统开发",
            mode="standard_live",
            feedback_mode="live",
            difficulty="standard",
        )
        self.assertEqual(result[1], 23)
        self.assertEqual(result[2], "第 1 题 / 共 5 题")
        self.assertIn(outcome.question, result[3])
        self.assertTrue(result[4]["visible"])
        self.assertEqual(result[5], "今日剩余 7 / 10 次")
        self.assertTrue(result[6]["interactive"])
        self.assertEqual(result[7]["value"], "")
        self.assertTrue(result[7]["interactive"])
        self.assertTrue(result[8]["interactive"])
        self.assertEqual(result[9], "")
        self.assertEqual(result[10], outcome.question)

    def test_interview_failure_preserves_workspace_and_reenables_start(self) -> None:
        with (
            patch.object(
                frontend.interview_service,
                "start",
                side_effect=InterviewValidationError("请输入目标岗位"),
            ),
            patch.object(frontend, "_quota_label", return_value="今日剩余 10 / 10 次"),
        ):
            result = frontend.start_interview_callback("", "JD", "简历", "token")

        self.assertIn("请输入目标岗位", result[0])
        for unchanged in result[1:5]:
            self.assertEqual(unchanged, {"__type__": "update"})
        self.assertEqual(result[5], "今日剩余 10 / 10 次")
        self.assertTrue(result[6]["interactive"])

    def test_clear_interview_workspace_removes_inputs_and_session(self) -> None:
        result = frontend.clear_interview_workspace()
        self.assertEqual(result[:4], ("", "", "", ""))
        self.assertIsNone(result[4])
        self.assertEqual(result[5:7], ("", ""))
        self.assertFalse(result[7]["visible"])
        self.assertTrue(result[8]["interactive"])
        self.assertEqual(result[9]["value"], "")
        self.assertEqual(result[10], "")
        self.assertTrue(result[11]["interactive"])

    def test_current_optimized_resume_can_fill_interview_context(self) -> None:
        result = frontend.use_optimized_resume_callback(
            "# 张三\n\n## 项目经历\n- 订单系统",
            "Java 后端工程师",
            "",
        )
        self.assertIn("订单系统", result[0])
        self.assertEqual(result[1], "Java 后端工程师")
        self.assertIn("已导入", result[2])

        missing = frontend.use_optimized_resume_callback("", "Java 后端工程师", "")
        self.assertEqual(missing[0], {"__type__": "update"})
        self.assertIn("先在简历诊断", missing[2])

    def test_submit_answer_callback_displays_feedback_and_next_question(self) -> None:
        outcome = InterviewAnswerOutcome(
            session_id=23,
            question_round=2,
            answer="使用监控和链路追踪定位瓶颈",
            feedback="回答覆盖了定位思路，建议补充指标阈值和验证顺序。",
            next_question="请说明如何保证消息消费幂等性？",
            follow_up_count=0,
            is_follow_up=False,
            is_finished=False,
        )
        with patch.object(
            frontend.interview_service,
            "submit_answer",
            return_value=outcome,
        ) as submit:
            result = frontend.submit_interview_answer_callback(
                outcome.answer,
                23,
                "token",
                "如何定位接口延迟？",
            )

        submit.assert_called_once_with(
            token="token",
            session_id=23,
            answer=outcome.answer,
            expected_question="如何定位接口延迟？",
        )
        self.assertIn("AI 反馈", result[0])
        self.assertIn("第 2 题", result[3])
        self.assertIn("消息消费幂等", result[4])
        self.assertEqual(result[5], outcome.next_question)
        self.assertTrue(result[1]["interactive"])
        self.assertTrue(result[2]["interactive"])

    def test_deferred_feedback_uses_dynamic_focused_progress(self) -> None:
        outcome = InterviewAnswerOutcome(
            session_id=23,
            question_round=2,
            answer="回答",
            feedback="",
            next_question="请说明你如何复盘并验证改进效果？",
            follow_up_count=1,
            is_follow_up=True,
            is_finished=False,
            total_questions=3,
            max_follow_up_count=1,
            feedback_mode="deferred",
        )
        with patch.object(
            frontend.interview_service,
            "submit_answer",
            return_value=outcome,
        ):
            result = frontend.submit_interview_answer_callback(
                "回答", 23, "token", "当前问题"
            )

        self.assertIn("面试结束后", result[0])
        self.assertEqual(result[3], "第 2 题 / 共 3 题 · 追问 1 / 1")

    def test_completed_interview_displays_saved_structured_report(self) -> None:
        report = InterviewReport(
            logic_score=82,
            professional_score=78,
            communication_score=85,
            summary="候选人分析过程较清楚，能够覆盖关键步骤，但技术取舍和量化验证仍需加强。",
            knowledge_gaps=("容量规划指标不够具体",),
            improvement_suggestions=("回答前先明确约束，再说明方案、取舍和验证方式",),
            reference_answers=(
                ReferenceAnswer(
                    question="如何定位线上接口延迟问题？",
                    answer="先确认影响范围，再结合监控、日志和链路追踪逐层定位并验证修复效果。",
                ),
            ),
        )
        outcome = InterviewAnswerOutcome(
            session_id=23,
            question_round=5,
            answer="先止损并定位根因",
            feedback="回答覆盖了主要步骤，建议补充量化恢复目标。",
            next_question="",
            follow_up_count=0,
            is_follow_up=False,
            is_finished=True,
            report=report,
        )
        with patch.object(
            frontend.interview_service,
            "submit_answer",
            return_value=outcome,
        ):
            result = frontend.submit_interview_answer_callback(
                outcome.answer, 23, "token", "请说明故障处理方法？"
            )

        self.assertIn("面试报告", result[0])
        self.assertIn("82 / 100", result[0])
        self.assertIn("容量规划", result[0])
        self.assertIn("参考回答", result[0])
        self.assertFalse(result[1]["interactive"])
        self.assertFalse(result[2]["interactive"])
        self.assertEqual(result[3], "已完成 5 题")
        self.assertIn("报告已保存", result[6])

    def test_diagnosis_callback_exposes_editable_complete_resume(self) -> None:
        outcome = DiagnosisOutcome(
            record_id=12,
            diagnosis=ResumeDiagnosis(
                score=88,
                missing_keywords=["FastAPI"],
                suggestions="- 增加接口设计细节",
                star_examples="- 情境/任务：订单系统；行动：开发接口；结果：[请补充真实数据]",
                optimized_text="# 张三\n\n## 项目经历\n- 负责订单接口",
            ),
        )
        with (
            patch.object(frontend.resume_service, "diagnose", return_value=outcome),
            patch.object(
                frontend.quota_service,
                "status",
                return_value=QuotaStatus(10, 1, date(2026, 9, 14)),
            ),
        ):
            result = frontend.diagnose_resume_callback(
                None,
                "一份足够完整的测试简历内容",
                "Python 后端工程师",
                "token",
            )
        self.assertIn("88", result[1])
        self.assertIn("STAR 改写示例", result[3])
        self.assertIn("[请补充真实数据]", result[3])
        self.assertEqual(result[4], outcome.diagnosis.optimized_text)
        self.assertEqual(result[5], 12)
        self.assertTrue(result[7]["visible"])
        self.assertEqual(result[8], "")
        self.assertIsNone(result[9]["value"])
        self.assertFalse(result[10]["visible"])
        self.assertFalse(result[11]["visible"])

    def test_generate_callback_returns_downloadable_pdf_and_closes_panel(self) -> None:
        destination = Path("data/exports/Python_新版简历_12.pdf")
        outcome = GeneratedResumeOutcome(
            record_id=12,
            document=ResumeDocument(
                basics=ResumeBasics(name="张三", headline="Python 后端工程师")
            ),
            pdf_path=destination,
        )
        with patch.object(
            frontend.resume_service,
            "generate_pdf_resume",
            return_value=outcome,
        ):
            result = frontend.generate_resume_callback(
                "token",
                12,
                "# 张三",
                "补充订单系统缓存改造经历",
            )
        self.assertFalse(result[0]["visible"])
        self.assertEqual(result[1], "")
        self.assertEqual(result[2]["value"], str(destination))
        self.assertTrue(result[2]["visible"])
        self.assertIn("PDF", result[3])
        self.assertIn("# 张三", result[4])

    def test_generate_submits_supplement_and_photo_together(self) -> None:
        outcome = GeneratedResumeOutcome(
            record_id=12,
            document=ResumeDocument(
                basics=ResumeBasics(name="张三", headline="Python 后端工程师")
            ),
            pdf_path=Path("resume.pdf"),
            template_id="technical",
        )
        with patch.object(
            frontend.resume_service,
            "generate_pdf_resume",
            return_value=outcome,
        ) as generate:
            result = frontend.generate_resume_callback(
                "token",
                12,
                "# 张三",
                "补充缓存改造",
                "portrait.png",
                "technical",
            )
        self.assertEqual(
            generate.call_args.kwargs["supplemental_experience"], "补充缓存改造"
        )
        self.assertEqual(generate.call_args.kwargs["photo_file"], "portrait.png")
        self.assertEqual(generate.call_args.kwargs["template_id"], "technical")
        self.assertIn("技术重点", result[3])

    def test_generate_failure_keeps_supplement_panel_and_input(self) -> None:
        with (
            patch.object(
                frontend.resume_service,
                "generate_pdf_resume",
                side_effect=ResumePDFError("浏览器不可用"),
            ),
            patch.object(frontend, "report_exception", return_value="error123") as report,
        ):
            result = frontend.generate_resume_callback(
                "token",
                12,
                "# 张三",
                "需要保留的补充信息",
            )
        self.assertTrue(result[0]["visible"])
        self.assertNotIn("value", result[1])
        self.assertFalse(result[2]["visible"])
        self.assertIn("浏览器不可用", result[3])
        self.assertIn("error123", result[3])
        report.assert_called_once()

    def test_unexpected_diagnosis_failure_returns_searchable_error_id(self) -> None:
        with (
            patch.object(
                frontend.resume_service,
                "diagnose",
                side_effect=RuntimeError("database disconnected"),
            ),
            patch.object(frontend, "report_exception", return_value="error456") as report,
            patch.object(frontend, "_quota_label", return_value=""),
        ):
            result = frontend.diagnose_resume_callback(
                None,
                "一份足够完整的测试简历内容",
                "Python 后端工程师",
                "token",
            )

        self.assertIn("系统异常", result[0])
        self.assertIn("error456", result[0])
        report.assert_called_once()

    def test_supplement_panel_open_and_cancel_contracts(self) -> None:
        opened = frontend.open_supplement_callback(12)
        self.assertTrue(opened[0]["visible"])
        self.assertEqual(opened[1], "")
        self.assertFalse(opened[2]["visible"])

        cancelled = frontend.cancel_supplement_callback()
        self.assertFalse(cancelled[0]["visible"])
        self.assertEqual(cancelled[1:], ("", ""))


if __name__ == "__main__":
    unittest.main()
