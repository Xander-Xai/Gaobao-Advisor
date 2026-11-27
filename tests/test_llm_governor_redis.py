"""Real Redis 7 tests for the LLM Resource Governor.

The governor budget has to be shared by every API and Worker instance, so it
is only meaningful against a real Redis. These tests use a random namespace
and delete only the keys they own; no FLUSHDB/FLUSHALL is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.services.llm_governor import (
    GovernanceConfig,
    LLMQuotaExceeded,
    LLMResourceGovernor,
    ProviderQuota,
    RedisFailureMode,
    RedisGovernorBackend,
)

REDIS_TEST_URL = os.getenv("REDIS_TEST_URL", "").strip()
ROOT = Path(__file__).resolve().parent.parent
TEST_REDIS_ROOT = f"gaobao:test:llm-governor:{uuid.uuid4().hex}"
pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(not REDIS_TEST_URL, reason="REDIS_TEST_URL is not set"),
]


@pytest.fixture
def redis_client():
    import redis

    client = redis.Redis.from_url(REDIS_TEST_URL, decode_responses=True, socket_connect_timeout=1, socket_timeout=2)
    if not client.ping():
        pytest.skip("Redis is not reachable")
    yield client
    keys = list(client.scan_iter(f"{TEST_REDIS_ROOT}:*"))
    if keys:
        client.delete(*keys)
    client.close()


def _governor(redis_client, namespace: str, **quota) -> LLMResourceGovernor:
    config = GovernanceConfig(
        enabled=True,
        namespace=namespace,
        redis_failure_mode=RedisFailureMode.FAIL_CLOSED,
        max_wait_seconds=0.0,
        default_lease_ttl_seconds=120.0,
        providers={"p1": ProviderQuota(**quota)},
    )
    return LLMResourceGovernor(config, backend=RedisGovernorBackend(redis_client, namespace=namespace))


def test_two_instances_share_one_provider_quota(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:shared-rpm"
    first = _governor(redis_client, namespace, rpm=3, max_concurrency=0, tpm=0)
    second = _governor(redis_client, namespace, rpm=3, max_concurrency=0, tpm=0)

    first.acquire("p1", model="m")
    second.acquire("p1", model="m")
    first.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        second.acquire("p1", model="m")
    assert excinfo.value.reason == "rpm"


def test_two_instances_share_one_concurrency_budget(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:shared-concurrency"
    first = _governor(redis_client, namespace, rpm=0, tpm=0, max_concurrency=1)
    second = _governor(redis_client, namespace, rpm=0, tpm=0, max_concurrency=1)

    lease = first.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        second.acquire("p1", model="m")
    assert excinfo.value.reason == "concurrency"
    first.release(lease)
    second.acquire("p1", model="m")


def test_the_concurrency_limit_is_never_exceeded_under_parallel_load(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:parallel"
    limit = 5
    governor = _governor(redis_client, namespace, rpm=0, tpm=0, max_concurrency=limit)

    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def _call(_index: int) -> bool:
        nonlocal in_flight, peak
        try:
            lease = governor.acquire("p1", model="m")
        except LLMQuotaExceeded:
            return False
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        governor.release(lease)
        return True

    with ThreadPoolExecutor(max_workers=20) as pool:
        admitted = list(pool.map(_call, range(60)))

    assert peak <= limit, f"the concurrency limit was exceeded: {peak} > {limit}"
    # The requests that did not fit were refused, not queued without a bound.
    assert sum(admitted) <= 60
    # Every slot was returned.
    assert redis_client.zcard(f"{{{namespace}:provider:p1}}:conc") == 0


def test_a_crashed_worker_lets_its_lease_be_reclaimed(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:crash"
    governor = _governor(redis_client, namespace, rpm=0, tpm=0, max_concurrency=1)
    governor.config.default_lease_ttl_seconds = 1.0
    short = LLMResourceGovernor(
        GovernanceConfig(
            enabled=True,
            namespace=namespace,
            default_lease_ttl_seconds=1.0,
            providers={"p1": ProviderQuota(rpm=0, tpm=0, max_concurrency=1)},
        ),
        backend=RedisGovernorBackend(redis_client, namespace=namespace),
    )

    short.acquire("p1", model="m")  # the worker dies here: no release
    with pytest.raises(LLMQuotaExceeded):
        short.acquire("p1", model="m")
    time.sleep(1.2)
    short.acquire("p1", model="m")  # the expired lease was reclaimed
    assert governor is not None


def test_the_token_reservation_is_shared_and_reconciled(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:tokens"
    governor = _governor(redis_client, namespace, rpm=0, tpm=100, max_concurrency=0)

    lease = governor.acquire("p1", model="m", estimated_input_tokens=60, reserved_output_tokens=30)
    assert redis_client.get(f"{{{namespace}:provider:p1}}:tpm:sum") == "90"
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m", estimated_input_tokens=20, reserved_output_tokens=30)
    assert excinfo.value.reason == "tpm"

    governor.reconcile(lease, input_tokens=60, output_tokens=5)
    assert redis_client.get(f"{{{namespace}:provider:p1}}:tpm:sum") == "65"
    governor.release(lease)


def test_two_processes_share_one_quota(redis_client):
    namespace = f"{TEST_REDIS_ROOT}:processes"
    code = (
        "from server.services.llm_governor import LLMResourceGovernor, GovernanceConfig, ProviderQuota, "
        "RedisGovernorBackend;"
        "import os, redis;"
        "client = redis.Redis.from_url(os.environ['REDIS_TEST_URL'], decode_responses=True);"
        "gov = LLMResourceGovernor(GovernanceConfig(enabled=True, namespace=os.environ['LIMIT_NS'], "
        "providers={'p1': ProviderQuota(rpm=2, max_concurrency=0, tpm=0)}), "
        "backend=RedisGovernorBackend(client, namespace=os.environ['LIMIT_NS']));"
        "\ntry:\n"
        "    gov.acquire('p1', model='m')\n"
        "    print('1')\n"
        "except Exception:\n"
        "    print('0')"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "REDIS_TEST_URL": REDIS_TEST_URL, "LIMIT_NS": namespace}
    processes = [
        subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)
        for _ in range(6)
    ]
    outputs = [process.communicate(timeout=60)[0].strip() for process in processes]
    assert all(process.returncode == 0 for process in processes)
    assert outputs.count("1") == 2


def test_an_unreachable_redis_fails_closed():
    config = GovernanceConfig(
        enabled=True,
        namespace=f"{TEST_REDIS_ROOT}:unreachable",
        redis_failure_mode=RedisFailureMode.FAIL_CLOSED,
        max_wait_seconds=0.0,
        providers={"p1": ProviderQuota(rpm=10)},
    )
    client = _UnreachableRedis()
    governor = LLMResourceGovernor(config, backend=RedisGovernorBackend(client, namespace=config.namespace))
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m")
    assert excinfo.value.reason == "redis_unavailable"


class _UnreachableRedis:
    def eval(self, *args, **kwargs):
        raise ConnectionError("redis is unreachable")


def test_the_gateway_budget_is_shared_between_two_instances(redis_client, monkeypatch: pytest.MonkeyPatch):
    """Two gateway instances (two API replicas) share one provider budget."""
    from server.services.llm_gateway import governed_client

    namespace = f"{TEST_REDIS_ROOT}:gateway"
    calls: list[dict] = []

    class _FakeResponse:
        choices = [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]
        usage = type("U", (), {"prompt_tokens": 5, "completion_tokens": 5})()
        model = "fake-model"

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse()

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: _Client())

    def _make() -> LLMResourceGovernor:
        config = GovernanceConfig(
            enabled=True,
            namespace=namespace,
            redis_failure_mode=RedisFailureMode.FAIL_CLOSED,
            max_wait_seconds=0.0,
            providers={"p1": ProviderQuota(rpm=2, max_concurrency=0, tpm=0)},
        )
        return LLMResourceGovernor(config, backend=RedisGovernorBackend(redis_client, namespace=namespace))

    def _client(governor: LLMResourceGovernor):
        return governed_client(
            "p1",
            operation="chat",
            governor=governor,
            config_override={
                "p1": {"api_key": "test-key", "base_url": "https://example.invalid/v1", "model": "fake-model"}
            },
        )

    _client(_make()).chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "one"}])
    _client(_make()).chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "two"}])
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        _client(_make()).chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "three"}])
    assert excinfo.value.reason == "rpm"
    assert len(calls) == 2
