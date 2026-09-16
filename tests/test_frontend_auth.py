"""Frontend callback contracts for session restoration and logout."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import frontend
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
            patch.object(frontend, "verify_login", return_value=("token", "登录成功")),
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
        with patch.object(frontend, "verify_login", return_value=(None, "验证码错误")):
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
                frontend.open_supplement_callback,
                frontend.login,
                frontend.restore_login,
                frontend.logout,
            }
            <= callbacks
        )

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
        )
        with patch.object(
            frontend.resume_service,
            "generate_pdf_resume",
            return_value=outcome,
        ) as generate:
            frontend.generate_resume_callback(
                "token", 12, "# 张三", "补充缓存改造", "portrait.png"
            )
        self.assertEqual(
            generate.call_args.kwargs["supplemental_experience"], "补充缓存改造"
        )
        self.assertEqual(generate.call_args.kwargs["photo_file"], "portrait.png")

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
