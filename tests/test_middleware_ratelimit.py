"""Tests for rate limit middleware."""

import time

from server.middleware.ratelimit import TokenBucketLimiter


def test_allows_requests_under_limit():
    limiter = TokenBucketLimiter(rate=10, capacity=10)
    for _ in range(10):
        assert limiter.allow("192.168.1.1") is True


def test_blocks_requests_over_limit():
    limiter = TokenBucketLimiter(rate=1, capacity=3)
    for _ in range(3):
        assert limiter.allow("10.0.0.1") is True
    assert limiter.allow("10.0.0.1") is False


def test_different_ips_independent():
    limiter = TokenBucketLimiter(rate=1, capacity=1)
    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("2.2.2.2") is True
    assert limiter.allow("1.1.1.1") is False


def test_token_refill():
    limiter = TokenBucketLimiter(rate=100, capacity=2)
    limiter.allow("test")
    limiter.allow("test")
    assert limiter.allow("test") is False
    time.sleep(0.05)
    assert limiter.allow("test") is True


def test_middleware_returns_429_with_retry_after():
    """Throttling must be a client-visible 429, not an unhandled 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.middleware.ratelimit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rate=1, capacity=1)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    blocked = client.get("/ping")
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after") == "1"
    assert "请求过于频繁" in blocked.json()["detail"]


def test_generic_trusted_proxies_are_honoured(monkeypatch):
    """Public endpoints can trust a configured proxy without weakening defaults."""
    from starlette.requests import Request

    from server.admin.client_ip import resolve_client_ip

    monkeypatch.delenv("ADMIN_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"198.51.100.1, 10.1.2.3")],
            "client": ("10.1.2.3", 12345),
        }
    )
    assert resolve_client_ip(request) == "198.51.100.1"


def test_untrusted_peer_ignores_forwarded_header(monkeypatch):
    from starlette.requests import Request

    from server.admin.client_ip import resolve_client_ip

    monkeypatch.delenv("ADMIN_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"198.51.100.1")],
            "client": ("203.0.113.9", 12345),
        }
    )
    assert resolve_client_ip(request) == "203.0.113.9"


def test_rejects_invalid_public_api_rate_limit_config():
    import pytest

    for rate, capacity in ((0, 1), (1, 0), (-1, 10), (10001, 10), (1, 10001)):
        with pytest.raises(ValueError):
            TokenBucketLimiter(rate=rate, capacity=capacity)
