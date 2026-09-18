"""Authentication behavior tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pbl_jobs_finder import Message
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.modules.auth import AuthService


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "auth.db"
        self.database = Database(f"sqlite:///{database_path.as_posix()}")
        self.database.initialize()
        self.now = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)
        self.auth = AuthService(self.database, clock=lambda: self.now)

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_send_code_validates_phone_and_prints_six_digits(self) -> None:
        self.assertEqual(
            self.auth.send_verification_code("123"),
            Message.failure("请输入有效的 11 位手机号"),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.auth.send_verification_code("13800138000")
        self.assertEqual(result, Message.success("验证码已发送，请查收"))
        printed = output.getvalue().strip().split(": ")[-1]
        self.assertRegex(printed, r"^\d{6}$")

    def test_wrong_code_can_retry_and_expired_code_is_rejected(self) -> None:
        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        code = output.getvalue().strip().split(": ")[-1]
        result = self.auth.verify_login(
            "13800138000", "000000" if code != "000000" else "111111"
        )
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertIn("验证码错误", result.message)
        result = self.auth.verify_login("13800138000", code)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.data)
        self.assertEqual(result.message, "登录成功")
        second_result = self.auth.verify_login("13800138000", code)
        self.assertFalse(second_result.success)
        self.assertIsNone(second_result.data)
        self.assertIn("不存在", second_result.message)

        with redirect_stdout(io.StringIO()):
            self.auth.send_verification_code("13900139000")
        self.now += timedelta(minutes=5)
        result = self.auth.verify_login("13900139000", code)
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertIn("过期", result.message)

    def test_token_is_reused_verified_and_revocable(self) -> None:
        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        code = output.getvalue().strip().split(": ")[-1]
        token = self.auth.verify_login("13800138000", code).data
        self.assertIsNotNone(token)
        self.assertEqual(
            self.auth.verify_token(token),
            {"phone": "13800138000", "nickname": "求职者"},
        )

        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        next_code = output.getvalue().strip().split(": ")[-1]
        reused = self.auth.verify_login("13800138000", next_code).data
        self.assertEqual(reused, token)
        self.assertTrue(self.auth.revoke_token(token))
        self.assertIsNone(self.auth.verify_token(token))

        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        expiry_code = output.getvalue().strip().split(": ")[-1]
        self.now += timedelta(days=7)
        expired = self.auth.verify_login(
            "13800138000", expiry_code
        )
        self.assertIsNone(expired.data)
        self.assertIn("过期", expired.message)

    def test_token_expires_after_seven_days(self) -> None:
        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        code = output.getvalue().strip().split(": ")[-1]
        token = self.auth.verify_login("13800138000", code).data
        self.now += timedelta(days=7)
        self.assertIsNone(self.auth.verify_token(token))


if __name__ == "__main__":
    unittest.main()
