"""Administrator rate limiting with memory and Redis backends.

The default local/test backend is an in-process sliding window. When Redis is
configured (or explicitly required), the limiter uses one atomic Lua script per
request: a sorted-set sliding window keyed by hashed client/identity, with a
Redis-side TTL. No GET/SET race is used to decide admission.

Security backend policy:

* ``ADMIN_RATE_LIMIT_BACKEND=auto`` (default): Redis when ``REDIS_URL`` is set;
  otherwise memory in non-production, unavailable in production.
* ``memory``: explicit local opt-in; unavailable in production.
* ``redis``: require Redis; unavailable if no URL.

If the backend is unavailable the caller must fail closed (503), never fall
back silently to process-local counters.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import deque
from typing import Any, Protocol

DEFAULT_NAMESPACE = "gaobao:admin:ratelimit:v1"


class SecurityBackendUnavailable(RuntimeError):
    """Redis/security backend cannot be used for an admission decision."""


class RateLimiter(Protocol):
    def check(self, key: str) -> tuple[bool, int]: ...


class SlidingWindowLimiter:
    """In-process sliding window, kept for local development and tests."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` and record the hit."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(self.window - (now - bucket[0])) + 1)
                return False, retry_after
            bucket.append(now)
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] >= cutoff}
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]
local t = redis.call('TIME')
local redis_now_ms = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
local newest = redis.call('ZREVRANGE', key, 0, 0, 'WITHSCORES')
local window_now = redis_now_ms
if newest[2] then
  window_now = math.max(window_now, tonumber(newest[2]))
end
local cutoff = window_now - window_ms
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = 1
  if oldest[2] then
    retry_ms = math.max(1, (tonumber(oldest[2]) + window_ms) - window_now)
  end
  return {0, math.max(1, math.ceil(retry_ms / 1000))}
end
redis.call('ZADD', key, window_now, member)
redis.call('PEXPIRE', key, window_ms)
return {1, 0}
"""


class RedisSlidingWindowLimiter:
    """Atomic Redis sorted-set sliding window.

    Only a keyed hash of the client/identity is passed as the key; the raw IP,
    username, and token never appear in Redis.
    """

    def __init__(
        self,
        *,
        url: str,
        limit: int,
        window_seconds: int,
        namespace: str = DEFAULT_NAMESPACE,
        client: Any | None = None,
    ) -> None:
        self.url = url
        self.limit = limit
        self.window_seconds = window_seconds
        self.window_ms = max(1, int(window_seconds * 1000))
        self.namespace = namespace
        self._client = client

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SecurityBackendUnavailable("redis package is not installed") from exc
        try:
            self._client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            self._client.ping()
        except Exception as exc:
            self._client = None
            raise SecurityBackendUnavailable("Redis security backend is unreachable") from exc
        return self._client

    def check(self, key: str) -> tuple[bool, int]:
        client = self._connect()
        member = f"{time.time_ns()}:{secrets.token_hex(4)}"
        redis_key = f"{self.namespace}:{key}"
        try:
            result = client.eval(
                _LUA_SLIDING_WINDOW,
                1,
                redis_key,
                str(self.limit),
                str(self.window_ms),
                member,
            )
        except Exception as exc:
            self._client = None
            raise SecurityBackendUnavailable("Redis security backend failed") from exc
        try:
            allowed, retry_after = int(result[0]), int(result[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise SecurityBackendUnavailable("Redis limiter returned an invalid response") from exc
        return bool(allowed), max(0, retry_after)

    def reset(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def security_backend() -> str:
    """Return ``redis``, ``memory`` or ``unavailable``; never silently fall back."""
    mode = os.getenv("ADMIN_RATE_LIMIT_BACKEND", "auto").strip().lower() or "auto"
    url = os.getenv("REDIS_URL", "").strip()
    env = os.getenv("APP_ENV", "development").strip().lower()
    if mode == "redis":
        return "redis" if url else "unavailable"
    if mode == "memory":
        return "memory" if env != "production" else "unavailable"
    if mode == "auto":
        if url:
            return "redis"
        return "memory" if env != "production" else "unavailable"
    return "unavailable"


class AdminLimiter:
    """Facade that selects the configured backend for one rate-limit scope."""

    def __init__(self, *, scope: str, limit: int, window_seconds: int) -> None:
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds
        self.memory = SlidingWindowLimiter(limit=limit, window_seconds=window_seconds)
        self._redis: RedisSlidingWindowLimiter | None = None
        self._redis_signature: tuple[str, str] | None = None

    def _redis_limiter(self) -> RedisSlidingWindowLimiter:
        url = os.getenv("REDIS_URL", "").strip()
        namespace = os.getenv("ADMIN_RATE_LIMIT_NAMESPACE", DEFAULT_NAMESPACE).strip() or DEFAULT_NAMESPACE
        signature = (url, namespace)
        if self._redis is None or self._redis_signature != signature:
            self._redis = RedisSlidingWindowLimiter(
                url=url,
                limit=self.limit,
                window_seconds=self.window_seconds,
                namespace=f"{namespace}:{self.scope}",
            )
            self._redis_signature = signature
        return self._redis

    def check(self, key: str) -> tuple[bool, int]:
        backend = security_backend()
        if backend == "redis":
            return self._redis_limiter().check(key)
        if backend == "memory":
            return self.memory.check(key)
        raise SecurityBackendUnavailable("administrator rate-limit backend is unavailable")

    def reset(self) -> None:
        self.memory.reset()
        if self._redis is not None:
            self._redis.reset()
        self._redis = None
        self._redis_signature = None
