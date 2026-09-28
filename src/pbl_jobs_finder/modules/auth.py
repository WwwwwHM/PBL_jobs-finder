"""Verification codes and opaque tokens with optional shared Redis state."""

from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from pbl_jobs_finder.messages import Message, MessageResult
from pbl_jobs_finder.models.database import Database, database
from pbl_jobs_finder.models.entities import User
from pbl_jobs_finder.models.repositories import get_or_create_user, get_user
from pbl_jobs_finder.modules.passwords import (
    PASSWORD_HINT,
    hash_password,
    valid_password,
    verify_password,
)
from pbl_jobs_finder.modules.redis_state import RedisState, configured_redis_state

PHONE_PATTERN = re.compile(r"^1\d{10}$")
CODE_PATTERN = re.compile(r"^\d{6}$")
VERIFICATION_CODE_TTL = timedelta(minutes=5)
TOKEN_TTL = timedelta(days=7)
PASSWORD_ATTEMPT_TTL = timedelta(minutes=15)
PASSWORD_ATTEMPT_LIMIT = 5


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
        state: RedisState | None = None,
    ) -> None:
        self.database = database_instance or database
        self.state = state
        self._clock = clock or (lambda: datetime.now(UTC))
        self._verification_codes: dict[str, _VerificationEntry] = {}
        self._tokens: dict[str, _TokenEntry] = {}
        self._phone_tokens: dict[str, str] = {}
        self._password_attempts: dict[str, tuple[int, datetime]] = {}
        self._lock = RLock()

    def register(self, phone: str, password: str, confirmation: str) -> MessageResult[str]:
        """Create a new account without replacing an existing phone identity."""
        phone = _normalize_phone(phone)
        if not PHONE_PATTERN.fullmatch(phone):
            return Message.failure("请输入有效的 11 位手机号")
        if not valid_password(password):
            return Message.failure(PASSWORD_HINT)
        if password != confirmation:
            return Message.failure("两次输入的密码不一致")
        self.database.initialize()
        existing_message = "该手机号已注册，请直接登录；验证码账号可登录后设置密码"
        with self.database.session() as session:
            if get_user(session, phone) is not None:
                return Message.failure(existing_message)
        encoded = hash_password(password)
        try:
            with self.database.session() as session:
                session.add(User(phone=phone, password_hash=encoded))
        except IntegrityError:
            with self.database.session() as session:
                if get_user(session, phone) is None:
                    raise
            return Message.failure(existing_message)
        return Message.success("注册成功", data=self._issue_token(phone))

    def login_password(self, phone: str, password: str) -> MessageResult[str]:
        phone = _normalize_phone(phone)
        if not PHONE_PATTERN.fullmatch(phone):
            return Message.failure("请输入有效的 11 位手机号")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            return Message.failure("手机号或密码错误")
        if not self._reserve_password_attempt(phone):
            return Message.failure("密码登录尝试过多，请 15 分钟后重试，或使用验证码登录")
        self.database.initialize()
        with self.database.session() as session:
            user = get_user(session, phone)
            encoded = user.password_hash if user is not None else None
        if not verify_password(password, encoded):
            return Message.failure("手机号或密码错误")
        self._clear_password_attempts(phone)
        return Message.success("登录成功", data=self._issue_token(phone))

    def set_password(self, token: str, password: str, confirmation: str) -> MessageResult[None]:
        """Let an authenticated code-only account set its initial password."""
        identity = self.verify_token(token)
        if identity is None:
            return Message.failure("登录已失效，请重新登录")
        if not valid_password(password):
            return Message.failure(PASSWORD_HINT)
        if password != confirmation:
            return Message.failure("两次输入的密码不一致")
        encoded = hash_password(password)
        with self.database.session() as session:
            result = session.execute(
                update(User)
                .where(User.phone == identity["phone"], User.password_hash.is_(None))
                .values(password_hash=encoded)
            )
            if result.rowcount != 1:
                return Message.failure("该账号已设置密码，请使用密码登录")
        self._clear_password_attempts(identity["phone"])
        return Message.success("密码设置成功，下次可使用手机号和密码登录")

    def _reserve_password_attempt(self, phone: str) -> bool:
        if self.state is not None:
            return self.state.reserve_password_attempt(phone)
        now = self._now()
        with self._lock:
            self._password_attempts = {
                key: entry for key, entry in self._password_attempts.items()
                if entry[1] > now
            }
            count, expiry = self._password_attempts.get(phone, (0, now + PASSWORD_ATTEMPT_TTL))
            if count >= PASSWORD_ATTEMPT_LIMIT:
                return False
            self._password_attempts[phone] = (count + 1, expiry)
            return True

    def _clear_password_attempts(self, phone: str) -> None:
        if self.state is not None:
            self.state.clear_password_attempts(phone)
        else:
            with self._lock:
                self._password_attempts.pop(phone, None)

    def _issue_token(self, phone: str) -> str:
        if self.state is not None:
            return self.state.issue_token(phone, secrets.token_urlsafe(32))
        now = self._now()
        with self._lock:
            token = self._existing_token(phone, now)
            if token is None:
                token = secrets.token_urlsafe(32)
                self._tokens[token] = _TokenEntry(phone=phone, expires_at=now + TOKEN_TTL)
                self._phone_tokens[phone] = token
            return token

    def send_verification_code(self, phone: str) -> MessageResult[None]:
        """Generate and print a six-digit code valid for five minutes."""

        phone = _normalize_phone(phone)
        if not PHONE_PATTERN.fullmatch(phone):
            return Message.failure("请输入有效的 11 位手机号")

        now = self._now()
        code = f"{secrets.randbelow(1_000_000):06d}"
        if self.state is not None:
            if not self.state.send_code(phone, code):
                return Message.failure("验证码发送过于频繁，请 60 秒后重试")
            print(f"[AUTH] verification code for {phone}: {code}")
            return Message.success("验证码已发送，请查收")
        with self._lock:
            self._verification_codes[phone] = _VerificationEntry(
                code=code,
                expires_at=now + VERIFICATION_CODE_TTL,
            )
        # Mock delivery is intentionally visible in the server console.
        print(f"[AUTH] verification code for {phone}: {code}")
        return Message.success("验证码已发送，请查收")

    def verify_login(self, phone: str, code: str) -> MessageResult[str]:
        """Verify a code and return a reused or newly-issued token."""

        phone = _normalize_phone(phone)
        code = (code or "").strip()
        if not PHONE_PATTERN.fullmatch(phone):
            return Message.failure("登录失败：请输入有效的 11 位手机号")
        if not CODE_PATTERN.fullmatch(code):
            return Message.failure("登录失败：请输入 6 位数字验证码")

        if self.state is not None:
            result = self.state.consume_code(phone, code)
            if result == 0:
                return Message.failure("登录失败：验证码已过期或不存在")
            if result == -1:
                return Message.failure("登录失败：验证码错误")
            self.database.initialize()
            with self.database.session() as session:
                get_or_create_user(session, phone)
            return Message.success("登录成功", data=self._issue_token(phone))

        now = self._now()
        with self._lock:
            entry = self._verification_codes.get(phone)
            if entry is None or entry.expires_at <= now:
                self._verification_codes.pop(phone, None)
                return Message.failure("登录失败：验证码已过期或不存在")
            if not hmac.compare_digest(entry.code, code):
                return Message.failure("登录失败：验证码错误")
            # A code is single-use after successful verification; wrong codes
            # leave it intact so the user can retry.
            self._verification_codes.pop(phone, None)

        self.database.initialize()
        with self.database.session() as session:
            get_or_create_user(session, phone)
        return Message.success("登录成功", data=self._issue_token(phone))

    def verify_token(self, token: str) -> dict[str, str] | None:
        """Return the authenticated user's public identity, or ``None``."""

        token = (token or "").strip()
        if not token:
            return None
        if self.state is not None:
            phone = self.state.token_phone(token)
            if phone is None:
                return None
            self.database.initialize()
            with self.database.session() as session:
                user = get_user(session, phone)
                if user is None:
                    self.state.revoke_token(token)
                    return None
                return {"phone": user.phone, "nickname": user.nickname}
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

        if self.state is not None:
            return self.state.revoke_token((token or "").strip())
        with self._lock:
            return self._revoke_token_locked((token or "").strip())

    def clear(self) -> None:
        """Clear in-memory state; intended for tests and process shutdown."""

        with self._lock:
            self._verification_codes.clear()
            self._tokens.clear()
            self._phone_tokens.clear()
            self._password_attempts.clear()

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


_default_auth_service = AuthService(state=configured_redis_state())


def send_verification_code(phone: str) -> MessageResult[None]:
    return _default_auth_service.send_verification_code(phone)


def verify_login(phone: str, code: str) -> MessageResult[str]:
    return _default_auth_service.verify_login(phone, code)


def register(phone: str, password: str, confirmation: str) -> MessageResult[str]:
    return _default_auth_service.register(phone, password, confirmation)


def login_password(phone: str, password: str) -> MessageResult[str]:
    return _default_auth_service.login_password(phone, password)


def set_password(token: str, password: str, confirmation: str) -> MessageResult[None]:
    return _default_auth_service.set_password(token, password, confirmation)


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
    "login_password",
    "register",
    "set_password",
    "revoke_token",
    "send_verification_code",
    "verify_login",
    "verify_token",
]
