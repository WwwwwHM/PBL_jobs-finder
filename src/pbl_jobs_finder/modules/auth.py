"""In-memory verification-code and token authentication for the MVP.

The MVP deliberately keeps short-lived authentication state in memory.  This
is sufficient for the single-process Gradio deployment described in the
project plan; Redis can replace the two dictionaries when multiple workers
are introduced.
"""

from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock

from pbl_jobs_finder.models.database import Database, database
from pbl_jobs_finder.models.repositories import get_or_create_user, get_user

PHONE_PATTERN = re.compile(r"^1\d{10}$")
CODE_PATTERN = re.compile(r"^\d{6}$")
VERIFICATION_CODE_TTL = timedelta(minutes=5)
TOKEN_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class _VerificationEntry:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _TokenEntry:
    phone: str
    expires_at: datetime


class AuthService:
    """Issue verification codes and authenticate users with opaque tokens.

    ``clock`` is injectable so expiry behavior can be tested without sleeping.
    The service owns a lock because Gradio callbacks may run concurrently.
    """

    def __init__(
        self,
        database_instance: Database | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database_instance or database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._verification_codes: dict[str, _VerificationEntry] = {}
        self._tokens: dict[str, _TokenEntry] = {}
        self._phone_tokens: dict[str, str] = {}
        self._lock = RLock()

    def send_verification_code(self, phone: str) -> tuple[bool, str]:
        """Generate and print a six-digit code valid for five minutes."""

        phone = _normalize_phone(phone)
        if not PHONE_PATTERN.fullmatch(phone):
            return False, "请输入有效的 11 位手机号"

        now = self._now()
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._verification_codes[phone] = _VerificationEntry(
                code=code,
                expires_at=now + VERIFICATION_CODE_TTL,
            )
        # Mock delivery is intentionally visible in the server console.
        print(f"[AUTH] verification code for {phone}: {code}")
        return True, "验证码已发送，请查收"

    def verify_login(self, phone: str, code: str) -> tuple[str | None, str]:
        """Verify a code and return a reused or newly-issued token."""

        phone = _normalize_phone(phone)
        code = (code or "").strip()
        if not PHONE_PATTERN.fullmatch(phone):
            return None, "登录失败：请输入有效的 11 位手机号"
        if not CODE_PATTERN.fullmatch(code):
            return None, "登录失败：请输入 6 位数字验证码"

        now = self._now()
        with self._lock:
            entry = self._verification_codes.get(phone)
            if entry is None or entry.expires_at <= now:
                self._verification_codes.pop(phone, None)
                return None, "登录失败：验证码已过期或不存在"
            if not hmac.compare_digest(entry.code, code):
                return None, "登录失败：验证码错误"
            # A code is single-use after successful verification; wrong codes
            # leave it intact so the user can retry.
            self._verification_codes.pop(phone, None)
            token = self._existing_token(phone, now)
            if token is None:
                token = secrets.token_urlsafe(32)
                self._tokens[token] = _TokenEntry(
                    phone=phone,
                    expires_at=now + TOKEN_TTL,
                )
                self._phone_tokens[phone] = token

        self.database.initialize()
        with self.database.session() as session:
            get_or_create_user(session, phone)
        return token, "登录成功"

    def verify_token(self, token: str) -> dict[str, str] | None:
        """Return the authenticated user's public identity, or ``None``."""

        token = (token or "").strip()
        if not token:
            return None
        now = self._now()
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None or entry.expires_at <= now:
                self._revoke_token_locked(token)
                return None
            phone = entry.phone

        self.database.initialize()
        with self.database.session() as session:
            user = get_user(session, phone)
            if user is None:
                with self._lock:
                    self._revoke_token_locked(token)
                return None
            return {"phone": user.phone, "nickname": user.nickname}

    def revoke_token(self, token: str) -> bool:
        """Invalidate a token and return whether it was active."""

        with self._lock:
            return self._revoke_token_locked((token or "").strip())

    def clear(self) -> None:
        """Clear in-memory state; intended for tests and process shutdown."""

        with self._lock:
            self._verification_codes.clear()
            self._tokens.clear()
            self._phone_tokens.clear()

    def _existing_token(self, phone: str, now: datetime) -> str | None:
        token = self._phone_tokens.get(phone)
        if token is None:
            return None
        entry = self._tokens.get(token)
        if entry is None or entry.expires_at <= now:
            self._revoke_token_locked(token)
            return None
        return token

    def _revoke_token_locked(self, token: str) -> bool:
        entry = self._tokens.pop(token, None)
        if entry is None:
            return False
        if self._phone_tokens.get(entry.phone) == token:
            self._phone_tokens.pop(entry.phone, None)
        return True

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def _normalize_phone(phone: str | None) -> str:
    return (phone or "").strip()


_default_auth_service = AuthService()


def send_verification_code(phone: str) -> tuple[bool, str]:
    return _default_auth_service.send_verification_code(phone)


def verify_login(phone: str, code: str) -> tuple[str | None, str]:
    return _default_auth_service.verify_login(phone, code)


def verify_token(token: str) -> dict[str, str] | None:
    return _default_auth_service.verify_token(token)


def revoke_token(token: str) -> bool:
    return _default_auth_service.revoke_token(token)


__all__ = [
    "CODE_PATTERN",
    "PHONE_PATTERN",
    "TOKEN_TTL",
    "VERIFICATION_CODE_TTL",
    "AuthService",
    "revoke_token",
    "send_verification_code",
    "verify_login",
    "verify_token",
]
