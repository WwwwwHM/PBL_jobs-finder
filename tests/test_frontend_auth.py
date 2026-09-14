"""Frontend callback contracts for session restoration and logout."""

import unittest
from datetime import date
from unittest.mock import patch

import frontend
from pbl_jobs_finder.modules.quota import AuthenticationError, QuotaStatus
from pbl_jobs_finder.modules.resume_diagnosis import DiagnosisOutcome, ResumeDiagnosis


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
        self.assertFalse(result[8]["visible"])

    def test_generate_callback_returns_downloadable_file(self) -> None:
        destination = "data/exports/Python_优化简历_12.docx"
        with patch.object(
            frontend.resume_service,
            "export_optimized_resume",
            return_value=destination,
        ):
            result = frontend.generate_resume_callback("token", 12, "# 张三")
        self.assertEqual(result[0]["value"], destination)
        self.assertTrue(result[0]["visible"])


if __name__ == "__main__":
    unittest.main()
