"""Real Redis 7 tests for the administrator sliding-window limiter."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.admin.limits import AdminLimiter, RedisSlidingWindowLimiter, SecurityBackendUnavailable
from server.admin.routes import _login_limiter
from server.deps import get_db
from server.main import app
from tests.conftest import auth_header, login

REDIS_TEST_URL = os.getenv("REDIS_TEST_URL", "").strip()
ROOT = Path(__file__).resolve().parent.parent
# Every key created by this test session lives under one random root. Cleanup
# only ever deletes this prefix; no FLUSHDB/FLUSHALL is used.
TEST_REDIS_ROOT = f"gaobao:test:limits:{uuid.uuid4().hex}"
pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(not REDIS_TEST_URL, reason="REDIS_TEST_URL is not set"),
]


def _cleanup_test_namespace(client) -> None:
    keys = list(client.scan_iter(f"{TEST_REDIS_ROOT}:*"))
    if keys:
        client.delete(*keys)


@pytest.fixture
def redis_client():
    import redis

    client = redis.Redis.from_url(REDIS_TEST_URL, decode_responses=True, socket_connect_timeout=1, socket_timeout=2)
    if not client.ping():
        pytest.skip("Redis is not reachable")
    yield client
    _cleanup_test_namespace(client)
    client.close()


def _limiter(client, *, limit: int, window: int = 60) -> RedisSlidingWindowLimiter:
    return RedisSlidingWindowLimiter(
        url=REDIS_TEST_URL,
        limit=limit,
        window_seconds=window,
        namespace=f"{TEST_REDIS_ROOT}:{uuid.uuid4().hex}",
        client=client,
    )


def test_single_instance_limit_retry_after_and_ttl(redis_client):
    limiter = _limiter(redis_client, limit=3)
    assert [limiter.check("client-a")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("client-a")
    assert allowed is False
    assert retry_after >= 1
    ttl = redis_client.pttl(f"{limiter.namespace}:client-a")
    assert 0 < ttl <= 60_000


def test_two_instances_share_the_same_counter(redis_client):
    first = _limiter(redis_client, limit=3)
    second = RedisSlidingWindowLimiter(
        url=REDIS_TEST_URL,
        limit=3,
        window_seconds=60,
        namespace=first.namespace,
        client=redis_client,
    )
    assert first.check("shared")[0] is True
    assert second.check("shared")[0] is True
    assert first.check("shared")[0] is True
    assert second.check("shared")[0] is False


def test_100_concurrent_requests_are_atomic(redis_client):
    limiter = _limiter(redis_client, limit=10)

    def call(_index: int) -> bool:
        return limiter.check("concurrent")[0]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(call, range(100)))
    assert sum(results) == 10
    assert redis_client.zcard(f"{limiter.namespace}:concurrent") == 10


def test_window_expiry_is_repeatable(redis_client):
    limiter = _limiter(redis_client, limit=1, window=1)
    assert limiter.check("expiry")[0] is True
    allowed, retry_after = limiter.check("expiry")
    assert allowed is False and retry_after >= 1
    time.sleep(retry_after + 0.1)
    assert limiter.check("expiry")[0] is True


def test_different_clients_are_isolated(redis_client):
    limiter = _limiter(redis_client, limit=1)
    assert limiter.check("client-a")[0] is True
    assert limiter.check("client-b")[0] is True
    assert limiter.check("client-a")[0] is False


def test_admin_limiter_uses_redis_backend(redis_client, monkeypatch):
    monkeypatch.setenv("ADMIN_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    monkeypatch.setenv("ADMIN_RATE_LIMIT_NAMESPACE", f"{TEST_REDIS_ROOT}:admin")
    first = AdminLimiter(scope="login", limit=2, window_seconds=60)
    second = AdminLimiter(scope="login", limit=2, window_seconds=60)
    assert first.check("shared")[0] is True
    assert second.check("shared")[0] is True
    assert first.check("shared")[0] is False


def test_two_processes_share_one_redis_counter(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:processes"
    code = (
        "from server.admin.limits import RedisSlidingWindowLimiter;"
        "import os;"
        "lim=RedisSlidingWindowLimiter(url=os.environ['REDIS_TEST_URL'],limit=5,window_seconds=60,"
        "namespace=os.environ['LIMIT_NS']);"
        "print('1' if lim.check('shared')[0] else '0')"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "REDIS_TEST_URL": REDIS_TEST_URL, "LIMIT_NS": namespace}
    processes = [
        subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
        for _ in range(10)
    ]
    outputs = [process.communicate(timeout=30)[0].strip() for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert outputs.count("1") == 5


def test_two_api_clients_share_login_limit(admin_factory, admin_env, monkeypatch):
    monkeypatch.setenv("ADMIN_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    monkeypatch.setenv("ADMIN_RATE_LIMIT_NAMESPACE", f"{TEST_REDIS_ROOT}:api")
    _login_limiter.reset()

    def override_get_db():
        session = admin_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    first = TestClient(app, client=("127.0.0.1", 12001))
    second = TestClient(app, client=("127.0.0.1", 12002))
    try:
        for index in range(8):
            client = first if index % 2 == 0 else second
            response = client.post(
                "/api/v1/admin/auth/login",
                json={"username": "admin", "password": "wrong-password-123"},
            )
            assert response.status_code == 401
        blocked = second.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "wrong-password-123"},
        )
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        first.close()
        second.close()


def test_revocation_survives_redis_key_loss(admin_client, redis_client):
    # Revocation is persisted in the database. Delete only test-owned Redis
    # keys to model data loss; never flush a shared Redis instance.
    token = login(admin_client, "admin")
    assert admin_client.post("/api/v1/admin/auth/logout", headers=auth_header(token)).status_code == 204
    redis_client.set(f"{TEST_REDIS_ROOT}:revocation-probe", "present")
    _cleanup_test_namespace(redis_client)
    assert redis_client.exists(f"{TEST_REDIS_ROOT}:revocation-probe") == 0
    assert admin_client.get("/api/v1/admin/auth/me", headers=auth_header(token)).status_code == 401


def test_namespace_cleanup_preserves_foreign_keys(redis_client):
    foreign_key = f"gaobao:foreign:{uuid.uuid4().hex}"
    redis_client.set(foreign_key, "keep")
    redis_client.set(f"{TEST_REDIS_ROOT}:owned", "delete")
    try:
        _cleanup_test_namespace(redis_client)
        assert redis_client.exists(f"{TEST_REDIS_ROOT}:owned") == 0
        assert redis_client.exists(foreign_key) == 1
    finally:
        redis_client.delete(foreign_key)


def test_unreachable_redis_is_explicit():
    limiter = RedisSlidingWindowLimiter(url="redis://127.0.0.1:1/0", limit=1, window_seconds=60)
    with pytest.raises(SecurityBackendUnavailable):
        limiter.check("client")
    limiter.reset()


def test_public_token_bucket_atomic_across_instances(redis_client):
    from server.middleware.ratelimit import RedisTokenBucketLimiter

    namespace = f"{TEST_REDIS_ROOT}:public"
    kwargs = {
        "url": REDIS_TEST_URL,
        "secret": "test-shared-secret",
        "rate": 0.001,
        "capacity": 5,
        "namespace": namespace,
        "client": redis_client,
    }
    first = RedisTokenBucketLimiter(**kwargs)
    second = RedisTokenBucketLimiter(**kwargs)

    def call(index: int) -> bool:
        return (first if index % 2 else second).allow("192.0.2.1")

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(call, range(50))) == 5
    key = first.key_for("192.0.2.1")
    assert key == second.key_for("192.0.2.1")
    assert "192.0.2.1" not in key
    assert redis_client.pttl(key) > 0
    assert second.allow("192.0.2.2")
