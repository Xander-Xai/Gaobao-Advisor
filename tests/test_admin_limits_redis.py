"""Real Redis 7 tests for the administrator sliding-window limiter."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.admin.limits import _LUA_SLIDING_WINDOW, AdminLimiter, RedisSlidingWindowLimiter, SecurityBackendUnavailable
from server.admin.routes import _login_limiter
from server.deps import get_db
from server.main import app
from server.middleware.ratelimit import _REDIS_TOKEN_BUCKET, RedisTokenBucketLimiter
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


def test_admin_window_keeps_attempts_after_clock_rollback(redis_client):
    time_read = (
        "local t = redis.call('TIME')\nlocal redis_now_ms = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)"
    )
    controlled_script = _LUA_SLIDING_WINDOW.replace(time_read, "local redis_now_ms = tonumber(ARGV[4])", 1)
    assert controlled_script != _LUA_SLIDING_WINDOW
    key = f"{TEST_REDIS_ROOT}:admin-clock:{uuid.uuid4().hex}"

    def check(now_ms: int, member: str) -> tuple[int, int]:
        decision = redis_client.eval(controlled_script, 1, key, "2", "60000", member, str(now_ms))
        return int(decision[0]), int(decision[1])

    assert check(100_000, "before-rollback") == (1, 0)
    assert check(90_000, "during-rollback") == (1, 0)
    assert redis_client.zscore(key, "during-rollback") == 100_000
    assert check(150_001, "after-clock-recovers")[0] == 0
    assert redis_client.zcard(key) == 2
    assert check(160_000, "window-expired") == (1, 0)


def test_python_and_java_bucket_scripts_do_not_replay_refill_after_clock_rollback(redis_client):
    import redis

    java_source = (
        ROOT / "java-services/gateway/src/main/java/com/xsuperplay/gaobao/gateway/"
        "FailClosedCatalogRateLimiterGatewayFilterFactory.java"
    ).read_text(encoding="utf-8")
    match = re.search(r'private static final String LUA = """\n(.*?)\n\s*""";', java_source, re.DOTALL)
    assert match is not None, "Java Gateway Lua script was not found"
    java_script = textwrap.dedent(match.group(1))

    time_read = "local t = redis.call('TIME')\nlocal now = tonumber(t[1]) + tonumber(t[2]) / 1000000"
    scripts = {
        "python": _REDIS_TOKEN_BUCKET.replace(time_read, "local now = tonumber(ARGV[3])", 1),
        "java": java_script.replace(time_read, "local now = tonumber(ARGV[3])", 1),
    }
    assert scripts["python"] != _REDIS_TOKEN_BUCKET
    assert scripts["java"] != java_script

    for language, script in scripts.items():
        key = f"{TEST_REDIS_ROOT}:bucket-clock:{language}:{uuid.uuid4().hex}"

        def run(now: int, bucket_key: str = key, bucket_script: str = script) -> tuple[int, int]:
            result = redis_client.eval(bucket_script, 1, bucket_key, "1", "4", str(now))
            return int(result[0]), int(result[1])

        assert [run(1_000)[0] for _ in range(4)] == [1, 1, 1, 1]
        assert run(1_001) == (1, 0)
        assert run(900) == (0, 1)
        assert float(redis_client.hget(key, "updated")) == 1_001
        assert run(1_001) == (0, 1)
        assert run(1_002) == (1, 0)
        assert float(redis_client.hget(key, "tokens")) <= 4

        forward_key = f"{key}:forward"
        assert run(2_000, forward_key) == (1, 0)
        assert [run(10_000, forward_key)[0] for _ in range(3)] == [1, 1, 1]
        assert float(redis_client.hget(forward_key, "tokens")) <= 4

        corrupt_key = f"{key}:corrupt"
        redis_client.hset(corrupt_key, mapping={"tokens": "5", "updated": "1000"})
        with pytest.raises(redis.exceptions.ResponseError, match="invalid token bucket state"):
            redis_client.eval(script, 1, corrupt_key, "1", "4", "1000")


def test_real_redis_public_middleware_isolates_ips_and_ignores_forged_forwarding(redis_client, monkeypatch):
    from fastapi import FastAPI

    from server.middleware.ratelimit import RateLimitMiddleware

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    monkeypatch.setenv("SESSION_SECRET", f"test-{uuid.uuid4().hex}")
    monkeypatch.delenv("ADMIN_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    app_with_limiter = FastAPI()
    app_with_limiter.add_middleware(RateLimitMiddleware, rate=0.001, capacity=1)

    @app_with_limiter.get("/ping")
    def ping():
        return {"ok": True}

    first = TestClient(app_with_limiter, client=("198.51.100.1", 12001))
    second = TestClient(app_with_limiter, client=("198.51.100.2", 12002))
    try:
        assert first.get("/ping", headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200
        blocked = first.get("/ping", headers={"X-Forwarded-For": "203.0.113.9"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "1000"
        assert second.get("/ping", headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200
    finally:
        first.close()
        second.close()


def test_public_token_bucket_is_shared_by_independent_processes(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:public-processes:{uuid.uuid4().hex}"
    code = (
        "from server.middleware.ratelimit import RedisTokenBucketLimiter;"
        "import os;"
        "lim=RedisTokenBucketLimiter(url=os.environ['REDIS_TEST_URL'],secret='shared-test-secret',"
        "rate=0.001,capacity=5,namespace=os.environ['LIMIT_NS']);"
        "print('1' if lim.allow('198.51.100.20') else '0')"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "REDIS_TEST_URL": REDIS_TEST_URL, "LIMIT_NS": namespace}
    processes = [
        subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
        for _ in range(10)
    ]
    outputs = [process.communicate(timeout=30)[0].strip() for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert outputs.count("1") == 5


def test_lost_public_bucket_key_resets_only_to_configured_burst(redis_client):
    limiter = RedisTokenBucketLimiter(
        url=REDIS_TEST_URL,
        secret="lost-key-test-secret",
        rate=0.001,
        capacity=4,
        namespace=f"{TEST_REDIS_ROOT}:lost-bucket:{uuid.uuid4().hex}",
        client=redis_client,
    )
    for _ in range(4):
        assert limiter.allow("198.51.100.21")
    assert not limiter.allow("198.51.100.21")
    redis_client.delete(limiter.key_for("198.51.100.21"))
    assert [limiter.allow("198.51.100.21") for _ in range(5)] == [True, True, True, True, False]


def test_corrupt_public_bucket_state_fails_closed(redis_client):
    limiter = RedisTokenBucketLimiter(
        url=REDIS_TEST_URL,
        secret="corrupt-state-test-secret",
        rate=1,
        capacity=2,
        namespace=f"{TEST_REDIS_ROOT}:corrupt-bucket:{uuid.uuid4().hex}",
        client=redis_client,
    )
    redis_client.hset(limiter.key_for("198.51.100.22"), mapping={"tokens": "3", "updated": "1000"})
    with pytest.raises(SecurityBackendUnavailable):
        limiter.check("198.51.100.22")
