"""Authentication behavior tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from pbl_jobs_finder import Message
from pbl_jobs_finder.models.database import Database
from pbl_jobs_finder.models.repositories import get_user
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

    def test_register_persists_salted_hash_and_login_survives_service_restart(self):
        password = "My password123 "
        result = self.auth.register(" 13800138000 ", password, password)
        self.assertTrue(result.success)
        self.assertEqual(self.auth.verify_token(result.data)["phone"], "13800138000")
        self.assertTrue(self.auth.register("13900139000", password, password).success)
        with self.database.session() as session:
            encoded = get_user(session, "13800138000").password_hash
            self.assertNotIn(password, encoded)
            self.assertTrue(encoded.startswith("pbkdf2_sha256$600000$"))
            self.assertNotEqual(encoded, get_user(session, "13900139000").password_hash)
        restarted = AuthService(self.database)
        login = restarted.login_password("13800138000", password)
        self.assertTrue(login.success)
        self.assertFalse(restarted.login_password("13800138000", password.strip()).success)
        self.assertTrue(restarted.revoke_token(login.data))
        self.assertIsNone(restarted.verify_token(login.data))

    def test_registration_validation_does_not_create_account(self):
        for phone, password, confirmation in (
            ("123", "Password123", "Password123"),
            ("13800138000", "short1", "short1"),
            ("13800138000", "abcdefgh", "abcdefgh"),
            ("13800138000", "12345678", "12345678"),
            ("13800138000", "Password123", "Different123"),
            ("13800138000", "A1" * 65, "A1" * 65),
        ):
            with self.subTest(phone=phone, length=len(password)):
                self.assertFalse(self.auth.register(phone, password, confirmation).success)
        with self.database.session() as session:
            self.assertIsNone(get_user(session, "13800138000"))

    def test_duplicate_registration_and_password_setting_cannot_overwrite_password(self):
        result = self.auth.register("13800138000", "Password123", "Password123")
        self.assertFalse(self.auth.register("13800138000", "Changed123", "Changed123").success)
        self.assertFalse(self.auth.set_password(result.data, "Changed123", "Changed123").success)
        self.assertTrue(self.auth.login_password("13800138000", "Password123").success)
        self.assertFalse(self.auth.login_password("13800138000", "Changed123").success)

    def test_code_account_can_set_password_only_after_authentication(self):
        with redirect_stdout(io.StringIO()) as output:
            self.auth.send_verification_code("13800138000")
        code = output.getvalue().strip().split(": ")[-1]
        token = self.auth.verify_login("13800138000", code).data
        self.assertFalse(self.auth.register("13800138000", "Password123", "Password123").success)
        self.assertFalse(self.auth.login_password("13800138000", "Password123").success)
        self.assertFalse(self.auth.set_password("forged-token", "Password123", "Password123").success)
        self.assertFalse(self.auth.set_password(token, "short1", "short1").success)
        self.assertFalse(self.auth.set_password(token, "Password123", "Mismatch123").success)
        self.assertTrue(self.auth.set_password(token, "Password123", "Password123").success)
        self.assertEqual(self.auth.login_password("13800138000", "Password123").data, token)
        with self.database.session() as session:
            self.assertEqual(get_user(session, "13800138000").nickname, "求职者")
        self.auth.revoke_token(token)
        self.assertFalse(self.auth.set_password(token, "Changed123", "Changed123").success)

    def test_password_failure_limit_expires_and_success_clears_attempts(self):
        self.auth.register("13800138000", "Password123", "Password123")
        for _ in range(5):
            result = self.auth.login_password("13800138000", "WrongPass123")
            self.assertEqual(result.message, "手机号或密码错误")
        self.assertIn("尝试过多", self.auth.login_password("13800138000", "Password123").message)
        self.now += timedelta(minutes=15)
        self.assertTrue(self.auth.login_password("13800138000", "Password123").success)
        for _ in range(4):
            self.auth.login_password("13800138000", "WrongPass123")
        self.assertTrue(self.auth.login_password("13800138000", "Password123").success)
        self.assertTrue(self.auth.login_password("13800138000", "Password123").success)
        self.assertEqual(self.auth.login_password("13900139000", "Password123").message, "手机号或密码错误")

    def test_password_auth_uses_shared_state_for_tokens_and_attempts(self):
        state = Mock()
        state.issue_token.return_value = "redis-token"
        state.token_phone.return_value = "13800138000"
        state.reserve_password_attempt.return_value = True
        auth = AuthService(self.database, state=state)
        result = auth.register("13800138000", "Password123", "Password123")
        self.assertEqual(result.data, "redis-token")
        self.assertTrue(auth.login_password("13800138000", "Password123").success)
        state.reserve_password_attempt.assert_called_once_with("13800138000")
        state.clear_password_attempts.assert_called_once_with("13800138000")
        self.assertEqual(auth.verify_token(result.data)["phone"], "13800138000")
        state.reserve_password_attempt.return_value = False
        self.assertIn("尝试过多", auth.login_password("13800138000", "Password123").message)


if __name__ == "__main__":
    unittest.main()
