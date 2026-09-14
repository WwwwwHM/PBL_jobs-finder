"""Frontend callback contracts for session restoration and logout."""

import unittest
from datetime import date
from unittest.mock import patch

import frontend
from pbl_jobs_finder.modules.quota import AuthenticationError, QuotaStatus


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
            {frontend.login, frontend.restore_login, frontend.logout} <= callbacks
        )


if __name__ == "__main__":
    unittest.main()
