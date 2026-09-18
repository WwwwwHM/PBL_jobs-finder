"""Global success and failure message result tests."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from pbl_jobs_finder import Message, MessageResult


class MessageTests(unittest.TestCase):
    def test_success_contains_message_and_payload(self) -> None:
        result = Message.success("登录成功", data="token")

        self.assertIsInstance(result, MessageResult)
        self.assertTrue(result.success)
        self.assertEqual(result.message, "登录成功")
        self.assertEqual(result.data, "token")
        self.assertEqual(
            result.to_dict(),
            {"success": True, "message": "登录成功", "data": "token"},
        )

    def test_failure_uses_the_same_result_contract(self) -> None:
        result = Message.failure("验证码错误")

        self.assertIsInstance(result, MessageResult)
        self.assertFalse(result.success)
        self.assertEqual(result.message, "验证码错误")
        self.assertIsNone(result.data)
        self.assertEqual(
            result.to_dict(),
            {"success": False, "message": "验证码错误", "data": None},
        )

    def test_factories_have_safe_default_messages(self) -> None:
        self.assertEqual(Message.success().message, "操作成功")
        self.assertEqual(Message.failure().message, "操作失败")

    def test_result_is_immutable_and_rejects_invalid_messages(self) -> None:
        result = Message.success()

        with self.assertRaises(FrozenInstanceError):
            result.message = "changed"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            Message.failure("  ")
        with self.assertRaisesRegex(TypeError, "boolean"):
            MessageResult(success=1, message="invalid")  # type: ignore[arg-type]

    def test_utility_class_cannot_be_instantiated(self) -> None:
        with self.assertRaisesRegex(TypeError, "utility class"):
            Message()


if __name__ == "__main__":
    unittest.main()
