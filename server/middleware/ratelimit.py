"""Token bucket rate limiter for FastAPI with TTL eviction."""

import hashlib
import hmac
import os
import time
from typing import Any

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from server.admin.client_ip import resolve_client_ip
from server.admin.limits import SecurityBackendUnavailable

# Evict entries idle longer than this (seconds)
_MAX_IDLE_SECONDS = 3600  # 1 hour
# Run eviction check every N requests
_EVICTION_INTERVAL = 500


class TokenBucketLimiter:
    """Per-IP token bucket rate limiter with TTL-based eviction."""

    def __init__(self, rate: float = 20, capacity: int = 40):
        if not 0 < rate <= 10000 or not 0 < capacity <= 10000:
            raise ValueError("Public API rate/burst must be positive and <= 10000")
        self.rate = rate
        self.capacity = capacity
        self._buckets: dict[str, tuple[float, float]] = {}
        self._request_count: int = 0

    def allow(self, ip: str) -> bool:
        now = time.monotonic()

        # Periodic eviction of idle entries
        self._request_count += 1
        if self._request_count % _EVICTION_INTERVAL == 0:
            self._evict_idle(now)

        if ip not in self._buckets:
            self._buckets[ip] = (self.capacity, now)
        tokens, last = self._buckets[ip]
        elapsed = now - last
        tokens = min(self.capacity, tokens + elapsed * self.rate)
        if tokens < 1:
            return False
        self._buckets[ip] = (tokens - 1, now)
        return True

    def _evict_idle(self, now: float) -> None:
        """Remove entries that have been idle (fully refilled) for too long."""
        idle_ips = [
            ip
            for ip, (tokens, last) in self._buckets.items()
            if now - last > _MAX_IDLE_SECONDS and tokens + (now - last) * self.rate >= self.capacity
        ]
        for ip in idle_ips:
            del self._buckets[ip]


# Redis TIME + one Lua transaction makes admission atomic across API replicas.
# Store only a keyed client digest and a short-lived bucket, never a raw IP.
_REDIS_TOKEN_BUCKET = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local previous = redis.call('HMGET', key, 'tokens', 'updated')
local tokens = tonumber(previous[1])
local updated = tonumber(previous[2])
if previous[1] == false and previous[2] == false then
  tokens = capacity
  updated = now
elseif not tokens or not updated or tokens < 0 or tokens > capacity or updated < 0 then
  return redis.error_reply('invalid token bucket state')
end
local effective_now = math.max(now, updated)
local elapsed = math.min(math.max(0, effective_now - updated), capacity / rate)
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local retry_seconds = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_seconds = math.max(1, math.ceil((1 - tokens) / rate))
end
redis.call('HSET', key, 'tokens', tokens, 'updated', effective_now)
redis.call('PEXPIRE', key, math.max(1000, math.ceil(capacity / rate * 2000)))
return {allowed, retry_seconds}
"""


class RedisTokenBucketLimiter:
    """Shared public HTTP admission; Redis failures must never admit a request."""

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        rate: float,
        capacity: float,
        namespace: str = "gaobao:public:ratelimit:v1",
        client: Any | None = None,
    ):
        if not 0.001 <= rate <= 10000 or not 1 <= capacity <= 10000:
            raise ValueError("Invalid industrial public API rate/burst")
        if not url or not secret:
            raise SecurityBackendUnavailable("Public rate-limit configuration unavailable")
        if client is None:
            import redis

            client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        self.client = client
        self.secret = secret.encode()
        self.rate = rate
        self.capacity = capacity
        self.namespace = namespace

    def key_for(self, ip: str) -> str:
        digest = hmac.new(self.secret, b"public-rate-limit:v1:" + ip.encode(), hashlib.sha256).hexdigest()
        return f"{self.namespace}:{digest}"

    def check(self, ip: str) -> tuple[bool, int]:
        try:
            result = self.client.eval(_REDIS_TOKEN_BUCKET, 1, self.key_for(ip), str(self.rate), str(self.capacity))
        except Exception as exc:
            raise SecurityBackendUnavailable("Public rate-limit backend unavailable") from exc
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise SecurityBackendUnavailable("Invalid public rate-limit decision")
        try:
            allowed, retry_after = int(result[0]), int(result[1])
        except (TypeError, ValueError) as exc:
            raise SecurityBackendUnavailable("Invalid public rate-limit decision") from exc
        if (
            allowed not in (0, 1)
            or retry_after < 0
            or (allowed == 1 and retry_after != 0)
            or (allowed == 0 and retry_after < 1)
        ):
            raise SecurityBackendUnavailable("Invalid public rate-limit decision")
        return allowed == 1, retry_after

    def allow(self, ip: str) -> bool:
        """Compatibility helper for callers that only need the admission bit."""
        return self.check(ip)[0]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware enforcing token bucket rate limits per IP.

    A middleware cannot raise ``HTTPException`` and expect FastAPI's exception
    handlers to see it: the exception escapes outside the router and becomes a
    500. Return a real 429 response instead so clients can back off.
    """

    def __init__(self, app, rate: float = 20, capacity: int = 40):
        super().__init__(app)
        self.shared = os.getenv("GAOBAO_RUNTIME_PROFILE", "") == "industrial"
        self.limiter: TokenBucketLimiter | RedisTokenBucketLimiter
        if self.shared:
            self.limiter = RedisTokenBucketLimiter(
                url=os.getenv("REDIS_URL", ""),
                secret=os.getenv("SESSION_SECRET", ""),
                rate=rate,
                capacity=capacity,
            )
        else:
            self.limiter = TokenBucketLimiter(rate=rate, capacity=capacity)

    async def dispatch(self, request: Request, call_next):
        # Internal liveness/readiness must retain their own dependency semantics.
        if self.shared and request.url.path in {"/api/v1/health/live", "/api/v1/health/ready"}:
            return await call_next(request)
        client_ip = resolve_client_ip(request)
        retry_after = 1
        try:
            if self.shared:
                assert isinstance(self.limiter, RedisTokenBucketLimiter)
                allowed, retry_after = await run_in_threadpool(self.limiter.check, client_ip)
            else:
                allowed = self.limiter.allow(client_ip)
        except SecurityBackendUnavailable:
            return JSONResponse(status_code=503, content={"detail": "服务暂不可用，请稍后再试"})
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": str(max(1, retry_after))},
            )
        return await call_next(request)
