"""Authenticated, thread-safe daily quota for the single-process MVP."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import RLock
from zoneinfo import ZoneInfo

from pbl_jobs_finder.config.settings import get_settings
from pbl_jobs_finder.modules.auth import verify_token


class AuthenticationError(ValueError):
    """The supplied token does not identify an active user."""


class QuotaExceededError(ValueError):
    """The user's daily quota has been exhausted."""


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    limit: int
    used: int
    day: date

    @property
    def remaining(self) -> int:
        return self.limit - self.used


class QuotaService:
    """Reserve before an AI call, retaining usage only on success.

    Validate inputs before entering ``operation``. Use it for a full resume
    diagnosis or a new interview, not for answers within an existing interview.
    """

    def __init__(
        self,
        *,
        daily_limit: int = 10,
        timezone_name: str = "Asia/Hong_Kong",
        token_verifier: Callable[[str], dict[str, str] | None] = verify_token,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if daily_limit <= 0:
            raise ValueError("daily_limit must be greater than zero")
        self.limit = daily_limit
        self.timezone = ZoneInfo(timezone_name)
        self._verify_token = token_verifier
        self._clock = clock or (lambda: datetime.now(UTC))
        self._usage: dict[str, tuple[date, int]] = {}
        self._lock = RLock()

    def status(self, token: str) -> QuotaStatus:
        phone = self._phone(token)
        with self._lock:
            day = self._day()
            return QuotaStatus(self.limit, self._used(phone, day), day)

    @contextmanager
    def operation(self, token: str) -> Generator[str, None, None]:
        """Yield the verified phone; roll back if the operation raises."""

        phone = self._phone(token)
        with self._lock:
            day = self._day()
            used = self._used(phone, day)
            if used >= self.limit:
                raise QuotaExceededError("今日免费次数已用完，请明日再试")
            self._usage[phone] = (day, used + 1)
        try:
            yield phone
        except BaseException:
            with self._lock:
                current_day, current_used = self._usage[phone]
                # An old request must not refund usage from the following day.
                if current_day == day:
                    self._usage[phone] = (day, current_used - 1)
            raise

    def _phone(self, token: str) -> str:
        user = self._verify_token(token)
        if user is None:
            raise AuthenticationError("登录已失效，请重新登录")
        return user["phone"]

    def _day(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(self.timezone).date()

    def _used(self, phone: str, day: date) -> int:
        stored_day, used = self._usage.get(phone, (day, 0))
        if stored_day != day:
            used = 0
        self._usage[phone] = (day, used)
        return used


_settings = get_settings()
quota_service = QuotaService(
    daily_limit=_settings.daily_quota, timezone_name=_settings.timezone
)
