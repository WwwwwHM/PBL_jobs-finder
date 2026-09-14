"""Daily quota boundaries, authentication and concurrent reservations."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from pbl_jobs_finder.modules.quota import (
    AuthenticationError,
    QuotaExceededError,
    QuotaService,
)


class QuotaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 14, 15, 59, tzinfo=UTC)
        self.users = {
            "token-a": {"phone": "13800138000"},
            "token-b": {"phone": "13900139000"},
        }
        self.quota = QuotaService(
            token_verifier=self.users.get,
            clock=lambda: self.now,
        )

    def test_tenth_allowed_eleventh_blocked_and_users_isolated(self) -> None:
        self.assertEqual(self.quota.status("token-a").remaining, 10)
        for _ in range(10):
            with self.quota.operation("token-a") as phone:
                self.assertEqual(phone, "13800138000")
        with (
            self.assertRaisesRegex(QuotaExceededError, "明日再试"),
            self.quota.operation("token-a"),
        ):
            self.fail("An exhausted operation must not run")
        self.assertEqual(self.quota.status("token-a").remaining, 0)
        self.assertEqual(self.quota.status("token-b").remaining, 10)

    def test_invalid_and_revoked_tokens_rejected(self) -> None:
        for token in ("", "invalid"):
            with self.assertRaises(AuthenticationError):
                self.quota.status(token)
            with self.assertRaises(AuthenticationError), self.quota.operation(token):
                self.fail("Invalid credentials must not run")
        del self.users["token-a"]
        with self.assertRaises(AuthenticationError):
            self.quota.status("token-a")

    def test_features_share_quota_and_interview_answers_do_not_count(self) -> None:
        with self.quota.operation("token-a"):
            pass  # Complete resume diagnosis.
        with self.quota.operation("token-a"):
            for _ in range(5):
                pass  # Answers are part of the same interview.
        self.assertEqual(self.quota.status("token-a").used, 2)

    def test_exception_refunds_reserved_usage(self) -> None:
        with self.assertRaises(TimeoutError), self.quota.operation("token-a"):
            self.assertEqual(self.quota.status("token-a").remaining, 9)
            raise TimeoutError("AI unavailable")
        self.assertEqual(self.quota.status("token-a").remaining, 10)

    def test_reset_at_local_midnight_and_old_failure_does_not_refund_today(
        self,
    ) -> None:
        with self.assertRaises(TimeoutError), self.quota.operation("token-a"):
            self.assertEqual(self.quota.status("token-a").used, 1)
            self.now += timedelta(minutes=1)
            self.assertEqual(self.quota.status("token-a").remaining, 10)
            with self.quota.operation("token-a"):
                pass
            raise TimeoutError("Yesterday's request failed")
        status = self.quota.status("token-a")
        self.assertEqual(status.day.isoformat(), "2026-09-15")
        self.assertEqual(status.used, 1)

    def test_parallel_calls_cannot_exceed_limit(self) -> None:
        def call(_: int) -> bool:
            try:
                with self.quota.operation("token-a"):
                    return True
            except QuotaExceededError:
                return False

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(call, range(50)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(self.quota.status("token-a").used, 10)

    def test_relogin_does_not_reset_phone_quota(self) -> None:
        with self.quota.operation("token-a"):
            pass
        self.users["new-token"] = self.users.pop("token-a")
        self.assertEqual(self.quota.status("new-token").used, 1)


if __name__ == "__main__":
    unittest.main()
