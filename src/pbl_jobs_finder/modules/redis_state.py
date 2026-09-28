"""Atomic short-lived state for a standalone Redis deployment."""

from __future__ import annotations

import hashlib
from functools import lru_cache

from pbl_jobs_finder.config import get_settings
from pbl_jobs_finder.exceptions import StateStoreError


class RedisState:
    def __init__(self, client: object, prefix: str = "pbl-jobs-finder:") -> None:
        self.client = client
        self.prefix = prefix

    def _call(self, method: str, *args: object, **kwargs: object):
        from redis.exceptions import RedisError

        try:
            return getattr(self.client, method)(*args, **kwargs)
        except RedisError as exc:
            raise StateStoreError() from exc

    def ping(self) -> bool:
        return bool(self._call("ping"))

    def _key(self, category: str, value: str) -> str:
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f"{self.prefix}{category}:{digest}"

    def send_code(self, phone: str, code: str) -> bool:
        # Cooldown and code replacement must succeed as a single operation.
        return bool(self._call("eval", """
            if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
            redis.call('SET', KEYS[1], '1', 'EX', 60)
            redis.call('SET', KEYS[2], ARGV[1], 'EX', 300)
            redis.call('DEL', KEYS[3])
            return 1
        """, 3, self._key("cooldown", phone), self._key("code", phone),
            self._key("attempts", phone), hashlib.sha256(code.encode()).hexdigest()))

    def consume_code(self, phone: str, code: str) -> int:
        return int(self._call("eval", """
            local expected = redis.call('GET', KEYS[1])
            if not expected then return 0 end
            if expected ~= ARGV[1] then
                local attempts = redis.call('INCR', KEYS[2])
                if attempts == 1 then redis.call('EXPIRE', KEYS[2], 300) end
                if attempts >= 5 then redis.call('DEL', KEYS[1]) end
                return -1
            end
            redis.call('DEL', KEYS[1], KEYS[2])
            return 1
        """, 2, self._key("code", phone), self._key("attempts", phone),
            hashlib.sha256(code.encode()).hexdigest()))

    def issue_token(self, phone: str, candidate: str) -> str:
        return str(self._call("eval", """
            local existing = redis.call('GET', KEYS[1])
            if existing then return existing end
            redis.call('SET', KEYS[1], ARGV[1], 'EX', 604800)
            redis.call('SET', KEYS[2], ARGV[2], 'EX', 604800)
            return ARGV[1]
        """, 2, self._key("phone", phone), self._key("token", candidate),
            candidate, phone))

    def token_phone(self, token: str) -> str | None:
        return self._call("get", self._key("token", token))

    def revoke_token(self, token: str) -> bool:
        phone = self.token_phone(token)
        if phone is None:
            return False
        return bool(self._call("eval", """
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                redis.call('DEL', KEYS[1])
            end
            return redis.call('DEL', KEYS[2])
        """, 2, self._key("phone", phone), self._key("token", token), token))

    def _quota_key(self, phone: str, day: str) -> str:
        return self._key("quota", f"{phone}:{day}")

    def quota_used(self, phone: str, day: str) -> int:
        return int(self._call("get", self._quota_key(phone, day)) or 0)

    def reserve_quota(self, phone: str, day: str, limit: int) -> bool:
        return bool(self._call("eval", """
            local used = tonumber(redis.call('GET', KEYS[1]) or '0')
            if used >= tonumber(ARGV[1]) then return 0 end
            redis.call('INCR', KEYS[1])
            if used == 0 then redis.call('EXPIRE', KEYS[1], 172800) end
            return 1
        """, 1, self._quota_key(phone, day), limit))

    def refund_quota(self, phone: str, day: str) -> None:
        self._call("eval", """
            local used = tonumber(redis.call('GET', KEYS[1]) or '0')
            if used > 0 then redis.call('DECR', KEYS[1]) end
            return 1
        """, 1, self._quota_key(phone, day))


@lru_cache(maxsize=1)
def configured_redis_state() -> RedisState | None:
    settings = get_settings()
    if settings.state_backend != "redis":
        return None
    from redis import Redis

    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=False,
        health_check_interval=30,
    )
    return RedisState(client, settings.redis_prefix)
