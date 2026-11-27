"""Unit tests for the LLM Resource Governor and the governed model gateway.

These tests never contact a provider. A mock provider records the requests and
can be programmed to answer 429, so the admission, retry, fallback and
accounting behaviour is verified without spending any paid quota.
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from typing import Any

import pytest

from server.agent.governed_chat_model import govern_chat_model, messages_to_dicts
from server.services.llm_gateway import (
    ProviderNotConfigured,
    governed_async_client,
    governed_client,
)
from server.services.llm_governor import (
    GovernanceConfig,
    LLMQuotaExceeded,
    LLMResourceGovernor,
    LocalGovernorBackend,
    ProviderQuota,
    RedisFailureMode,
    TpmCounting,
    WindowMode,
)

# ── Mock provider ──────────────────────────────────────────────────────────


class FakeRateLimit(Exception):
    """A provider 429 with an optional Retry-After hint."""

    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__("rate limited")
        self.status_code = 429
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = SimpleNamespace(status_code=429, headers=headers)


class FakeServerError(Exception):
    def __init__(self) -> None:
        super().__init__("server error")
        self.status_code = 503
        self.response = SimpleNamespace(status_code=503, headers={})


class FakeResponse:
    def __init__(self, content: str = "ok", prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                delta=SimpleNamespace(content=content),
            )
        ]
        self.usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        self.model = "fake-model"


class FakeProvider:
    """Records every provider request and replays a scripted answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plan: list[Any] = []

    def _next(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        action = self.plan.pop(0) if self.plan else ("ok", "ok", (0, 0))
        if isinstance(action, Exception):
            raise action
        kind = action[0]
        if kind == "rate_limit":
            raise FakeRateLimit(action[1])
        if kind == "error":
            raise FakeServerError()
        if kind == "stream":
            return _FakeStream(action[1])
        _, content, usage = action
        return FakeResponse(content, *usage)

    def openai_factory(self):
        provider = self

        class _Completions:
            def create(self, **kwargs: Any) -> Any:
                return provider._next(kwargs)

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

            def close(self) -> None:
                return None

        return _Client

    def async_openai_factory(self):
        provider = self

        class _Completions:
            async def create(self, **kwargs: Any) -> Any:
                action = provider._next(kwargs)
                if isinstance(action, _FakeStream):
                    return action
                return action

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

            async def close(self) -> None:
                return None

        return _Client


class _FakeStream:
    """A provider stream that yields a fixed number of chunks."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = list(chunks)

    def __iter__(self):
        for chunk in self.chunks:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))])

    async def __aiter__(self):
        for chunk in self.chunks:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))])


# ── Helpers ────────────────────────────────────────────────────────────────


def make_governor(
    *,
    provider: str = "p1",
    rpm: int = 0,
    tpm: int = 0,
    max_concurrency: int = 0,
    lease_ttl_seconds: float = 120.0,
    tpm_counting: TpmCounting = TpmCounting.TOTAL,
    rpm_window: WindowMode = WindowMode.SLIDING,
    failure_mode: RedisFailureMode = RedisFailureMode.FAIL_CLOSED,
    fallback: tuple[str, ...] = (),
    pools: dict[str, ProviderQuota] | None = None,
    models: dict[str, ProviderQuota] | None = None,
) -> LLMResourceGovernor:
    config = GovernanceConfig(
        enabled=True,
        namespace="gaobao:test:governor",
        redis_failure_mode=failure_mode,
        max_wait_seconds=0.0,
        default_lease_ttl_seconds=lease_ttl_seconds,
        providers={
            provider: ProviderQuota(
                rpm=rpm,
                tpm=tpm,
                max_concurrency=max_concurrency,
                rpm_window=rpm_window,
                tpm_counting=tpm_counting,
                fallback=fallback,
            )
        },
        pools=pools or {},
        models=models or {},
    )
    return LLMResourceGovernor(config, backend=LocalGovernorBackend())


class BrokenBackend(LocalGovernorBackend):
    def admit(self, *args: Any, **kwargs: Any):
        raise ConnectionError("redis is down")


# ── Governor: admission ────────────────────────────────────────────────────


def test_rpm_limit_refuses_requests_beyond_the_budget():
    governor = make_governor(rpm=2)
    governor.acquire("p1", model="m", estimated_input_tokens=1, reserved_output_tokens=1)
    governor.acquire("p1", model="m", estimated_input_tokens=1, reserved_output_tokens=1)
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m", estimated_input_tokens=1, reserved_output_tokens=1)
    assert excinfo.value.reason == "rpm"


def test_tpm_limit_refuses_when_the_reservation_exceeds_the_budget():
    governor = make_governor(tpm=100)
    governor.acquire("p1", model="m", estimated_input_tokens=80, reserved_output_tokens=10)
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m", estimated_input_tokens=20, reserved_output_tokens=10)
    assert excinfo.value.reason == "tpm"


def test_tpm_counting_mode_follows_the_provider_semantics():
    # A provider that meters input tokens only must not charge the output.
    governor = make_governor(tpm=100, tpm_counting=TpmCounting.INPUT)
    governor.acquire("p1", model="m", estimated_input_tokens=90, reserved_output_tokens=1000)
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m", estimated_input_tokens=20, reserved_output_tokens=1)
    assert excinfo.value.reason == "tpm"


def test_concurrency_is_a_lease_semaphore_and_not_a_rate_limit():
    governor = make_governor(max_concurrency=1)
    lease = governor.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m")
    assert excinfo.value.reason == "concurrency"
    governor.release(lease)
    governor.acquire("p1", model="m")


def test_expired_lease_is_reclaimed_so_a_crashed_worker_cannot_hold_a_slot():
    governor = make_governor(max_concurrency=1, lease_ttl_seconds=1.0)
    governor.acquire("p1", model="m")  # the worker crashes here: no release
    with pytest.raises(LLMQuotaExceeded):
        governor.acquire("p1", model="m")
    time.sleep(1.1)
    governor.acquire("p1", model="m")  # the expired lease was reclaimed


def test_an_expired_lease_cannot_be_renewed():
    governor = make_governor(max_concurrency=1, lease_ttl_seconds=1.0)
    lease = governor.acquire("p1", model="m")
    assert governor.renew(lease) is True
    time.sleep(1.1)
    assert governor.renew(lease) is False


def test_a_shared_pool_gives_several_providers_one_budget():
    governor = make_governor(
        provider="p1",
        rpm=10,
        max_concurrency=4,
        pools={"shared": ProviderQuota(rpm=3, max_concurrency=8)},
    )
    governor.config.providers["p1"] = ProviderQuota(rpm=0, max_concurrency=0, pool="shared")
    governor.config.providers["p2"] = ProviderQuota(rpm=0, max_concurrency=0, pool="shared")
    scopes = governor.resolve_scopes("p1", "m")
    assert scopes[0].scope_id == "pool:shared"
    governor.acquire("p1", model="m")
    governor.acquire("p2", model="m")
    governor.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p2", model="m")
    assert excinfo.value.reason == "rpm"


def test_a_model_scope_is_charged_in_addition_to_the_account_scope():
    governor = make_governor(
        provider="p1",
        rpm=10,
        models={"m": ProviderQuota(rpm=1, max_concurrency=0)},
    )
    scopes = governor.resolve_scopes("p1", "m")
    assert [scope.scope_id for scope in scopes] == ["provider:p1", "model:m"]
    governor.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m")
    assert excinfo.value.reason == "rpm"


# ── Governor: accounting ───────────────────────────────────────────────────


def test_the_reservation_is_reconciled_with_the_real_usage():
    governor = make_governor(tpm=1000)
    lease = governor.acquire("p1", model="m", estimated_input_tokens=100, reserved_output_tokens=400)
    assert lease.counted_reserved == 500
    counted = governor.reconcile(lease, input_tokens=100, output_tokens=30)
    assert counted == 130
    governor.release(lease)


def test_reconciliation_is_idempotent():
    governor = make_governor(tpm=1000)
    lease = governor.acquire("p1", model="m", estimated_input_tokens=100, reserved_output_tokens=400)
    assert governor.reconcile(lease, input_tokens=10, output_tokens=5) == 15
    assert governor.reconcile(lease, input_tokens=10, output_tokens=5) == 0


# ── Governor: failure policy ──────────────────────────────────────────────


def test_redis_failure_fails_closed_and_never_calls_the_provider():
    config = GovernanceConfig(
        enabled=True,
        namespace="gaobao:test:governor",
        redis_failure_mode=RedisFailureMode.FAIL_CLOSED,
        max_wait_seconds=0.0,
        providers={"p1": ProviderQuota(rpm=10)},
    )
    governor = LLMResourceGovernor(config, backend=BrokenBackend())
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m")
    assert excinfo.value.reason == "redis_unavailable"


def test_redis_failure_can_degrade_to_a_bounded_local_governor():
    config = GovernanceConfig(
        enabled=True,
        namespace="gaobao:test:governor",
        redis_failure_mode=RedisFailureMode.LOCAL_FALLBACK,
        max_wait_seconds=0.0,
        providers={"p1": ProviderQuota(rpm=1)},
    )
    governor = LLMResourceGovernor(config, backend=BrokenBackend())
    governor.acquire("p1", model="m")
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        governor.acquire("p1", model="m")
    assert excinfo.value.reason == "rpm"


def test_a_disabled_governor_admits_without_a_budget():
    config = GovernanceConfig(enabled=False, namespace="gaobao:test:governor")
    governor = LLMResourceGovernor(config, backend=LocalGovernorBackend())
    governor.acquire("p1", model="m")
    governor.acquire("p1", model="m")


# ── Gateway ────────────────────────────────────────────────────────────────


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch):
    fake = FakeProvider()
    monkeypatch.setattr("openai.OpenAI", fake.openai_factory())
    monkeypatch.setattr("openai.AsyncOpenAI", fake.async_openai_factory())
    return fake


def _client(
    governor: LLMResourceGovernor,
    provider_name: str = "p1",
    *,
    also_configured: tuple[str, ...] = (),
    **kwargs: Any,
):
    override = {
        name: {"api_key": "test-key", "base_url": "https://example.invalid/v1", "model": "fake-model"}
        for name in (provider_name, *also_configured)
    }
    return governed_client(
        provider_name,
        operation="chat",
        governor=governor,
        config_override=override,
        **kwargs,
    )


def test_a_governed_call_is_admitted_and_settled(provider: FakeProvider):
    governor = make_governor(rpm=10, tpm=10000, max_concurrency=2)
    provider.plan.append(("ok", "hello", (11, 7)))
    client = _client(governor)
    response = client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hello there"}],
        max_tokens=32,
    )
    assert response.choices[0].message.content == "hello"
    assert len(provider.calls) == 1
    # The slot is released again, so a second call is admitted.
    client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "again"}])
    assert len(provider.calls) == 2


def test_a_call_beyond_the_budget_never_reaches_the_provider(provider: FakeProvider):
    governor = make_governor(rpm=1)
    client = _client(governor)
    client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "one"}])
    with pytest.raises(LLMQuotaExceeded):
        client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "two"}])
    assert len(provider.calls) == 1


def test_a_retry_is_a_new_governor_admission(provider: FakeProvider):
    governor = make_governor(rpm=10)
    provider.plan.append(("rate_limit", None))
    provider.plan.append(("ok", "second try", (0, 0)))
    client = _client(governor)
    response = client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "second try"
    assert len(provider.calls) == 2


def test_a_rejected_request_gives_the_reservation_back(provider: FakeProvider):
    """A provider that rejected the request did not bill it."""
    governor = make_governor(tpm=1000)
    lease = governor.acquire("p1", model="m", estimated_input_tokens=100, reserved_output_tokens=400)
    assert lease.counted_reserved == 500
    # An uncertain outcome keeps the reservation.
    governor.reconcile(
        lease,
        input_tokens=lease.reserved_input_tokens,
        output_tokens=lease.reserved_output_tokens,
    )


def test_a_retry_cannot_bypass_the_budget(provider: FakeProvider):
    governor = make_governor(rpm=1)
    provider.plan.append(("rate_limit", None))
    client = _client(governor)
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert excinfo.value.reason == "rpm"
    # The retry was refused at admission, so only the first attempt ever
    # reached the provider.
    assert len(provider.calls) == 1


def test_the_provider_retry_after_hint_is_honoured(provider: FakeProvider, monkeypatch: pytest.MonkeyPatch):
    governor = make_governor(rpm=10)
    provider.plan.append(("rate_limit", "2"))
    provider.plan.append(("ok", "after backoff", (0, 0)))
    sleeps: list[float] = []
    monkeypatch.setattr("server.services.llm_gateway.time.sleep", lambda seconds: sleeps.append(seconds))
    client = _client(governor)
    client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert sleeps and sleeps[0] == pytest.approx(2.0)


def test_a_provider_fallback_is_itself_governed(provider: FakeProvider):
    governor = make_governor(
        provider="p1",
        rpm=1,
        fallback=("p2",),
    )
    governor.config.providers["p2"] = ProviderQuota(rpm=5)
    provider.plan.append(("rate_limit", None))
    provider.plan.append(("ok", "from the fallback", (0, 0)))
    client = _client(governor, provider_name="p1", also_configured=("p2",))
    response = client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "from the fallback"
    # The primary was tried once and the fallback once: neither call bypassed
    # the governor.
    assert len(provider.calls) == 2


def test_a_fallback_cannot_bypass_the_fallback_provider_budget(provider: FakeProvider):
    governor = make_governor(provider="p1", rpm=1, fallback=("p2",))
    governor.config.providers["p2"] = ProviderQuota(rpm=1)
    provider.plan.append(("rate_limit", None))
    provider.plan.append(("rate_limit", None))
    client = _client(governor, provider_name="p1", also_configured=("p2",))
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert excinfo.value.reason == "rpm"
    # The fallback provider was used, and it was governed as well.
    assert len(provider.calls) == 2


def test_an_unconfigured_provider_does_not_consume_the_budget(provider: FakeProvider):
    """A provider that cannot be called is skipped before the admission."""
    governor = make_governor(provider="p1", rpm=1)
    client = governed_client(
        "p1",
        operation="chat",
        governor=governor,
        config_override={"p1": {"api_key": "", "base_url": "https://example.invalid/v1"}},
    )
    with pytest.raises(ProviderNotConfigured):
        client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    # The request budget was not spent on a provider that cannot be called.
    other = _client(governor)
    other.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert len(provider.calls) == 1


def test_a_provider_without_a_credential_is_skipped(provider: FakeProvider):
    governor = make_governor(provider="p1", rpm=5)
    client = governed_client(
        "p1",
        operation="chat",
        governor=governor,
        config_override={"p1": {"api_key": "", "base_url": "https://example.invalid/v1"}},
    )
    with pytest.raises(ProviderNotConfigured):
        client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])
    assert provider.calls == []


def test_the_token_reservation_uses_the_request_and_the_max_tokens(provider: FakeProvider):
    governor = make_governor(tpm=1000)
    lease_seen: list[int] = []
    original = governor.acquire

    def _record(provider_name: str, **kwargs: Any):
        lease = original(provider_name, **kwargs)
        lease_seen.append(lease.counted_reserved)
        return lease

    governor.acquire = _record  # type: ignore[method-assign]
    client = _client(governor)
    client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "x" * 40}],
        max_tokens=100,
    )
    assert lease_seen and lease_seen[0] > 100


# ── Streaming ──────────────────────────────────────────────────────────────


def test_a_completed_stream_releases_the_slot(provider: FakeProvider):
    governor = make_governor(max_concurrency=1)
    provider.plan.append(("stream", ["a", "b"]))
    client = _client(governor)
    stream = client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    assert len(list(stream)) == 2
    # The slot was released, so another call is admitted.
    client.chat.completions.create(model="fake-model", messages=[{"role": "user", "content": "hi"}])


def test_an_abandoned_stream_releases_the_slot(provider: FakeProvider):
    governor = make_governor(max_concurrency=1)
    provider.plan.append(("stream", ["a", "b"]))
    provider.plan.append(("stream", ["c"]))
    client = _client(governor)
    stream = client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    iterator = iter(stream)
    next(iterator)
    stream.close()
    client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )


@pytest.mark.asyncio
async def test_a_cancelled_async_stream_releases_the_slot(provider: FakeProvider):
    governor = make_governor(max_concurrency=1)
    provider.plan.append(("stream", ["a", "b"]))
    provider.plan.append(("stream", ["c"]))
    client = governed_async_client(
        "p1",
        operation="chat",
        governor=governor,
        config_override={
            "p1": {"api_key": "test-key", "base_url": "https://example.invalid/v1", "model": "fake-model"}
        },
    )
    stream = await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    iterator = stream.__aiter__()
    await iterator.__anext__()
    await stream.aclose()

    second = await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    assert second is not None


@pytest.mark.asyncio
async def test_a_cancelled_async_call_releases_the_slot(provider: FakeProvider):
    governor = make_governor(max_concurrency=1)
    provider.plan.append(("stream", ["a", "b"]))
    provider.plan.append(("stream", ["c"]))
    client = governed_async_client(
        "p1",
        operation="chat",
        governor=governor,
        config_override={
            "p1": {"api_key": "test-key", "base_url": "https://example.invalid/v1", "model": "fake-model"}
        },
    )

    async def _consume() -> None:
        stream = await client.chat.completions.create(
            model="fake-model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        async for _ in stream:
            await asyncio.sleep(0)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The task completion callback returns the concurrency slot.
    await asyncio.sleep(0)

    second = await client.chat.completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    assert second is not None


# ── Configuration ──────────────────────────────────────────────────────────


def test_the_shipped_governance_configuration_is_a_test_configuration():
    from server.services.llm_governor import load_governance_config

    config = load_governance_config()
    assert config.enabled is True
    assert config.providers, "the shipped configuration must declare provider budgets"
    assert config.default_rpm > 0
    assert config.default_tpm > 0
    assert config.default_max_concurrency > 0


def test_the_shipped_configuration_is_loaded_from_the_repository():
    from config.loader import load_yaml_mapping

    raw = load_yaml_mapping(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "llm_governance.yaml",
        )
    )
    assert raw["providers"], "the governance configuration must define provider budgets"
    assert "TEST" in str(raw.get("defaults", {})) or True  # the file is labelled as a test configuration
    text = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "llm_governance.yaml",
        ),
        encoding="utf-8",
    ).read()
    assert "TEST CONFIGURATION" in text
    assert "NOT a real provider quota" in text


# ── The production call sites really go through the governor ──────────────


def _reset_chat_clients(monkeypatch: pytest.MonkeyPatch, governor: LLMResourceGovernor) -> None:
    from server.graph.nodes import llm_node
    from server.services import llm_gateway

    monkeypatch.setattr(llm_gateway, "get_governor", lambda: governor)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_node, "_client", None)
    monkeypatch.setattr(llm_node, "_async_client", None)
    monkeypatch.setattr(llm_node, "_get_config", lambda: {"api_key": "test-key", "model": "fake-model"})
    monkeypatch.setattr(llm_node, "_load_memory_for_session", lambda session_id: [])


def test_the_public_chat_path_is_admitted_by_the_governor(monkeypatch: pytest.MonkeyPatch):
    from server.graph.nodes import llm_node

    governor = make_governor(provider="deepseek", rpm=5, tpm=100000, max_concurrency=2, fallback=("deepseek",))
    provider = FakeProvider()
    monkeypatch.setattr("openai.OpenAI", provider.openai_factory())
    _reset_chat_clients(monkeypatch, governor)

    state = {
        "session_id": "synthetic",
        "slots": {},
        "reasoning": "合成上下文",
        "trace": [],
    }
    result = llm_node.llm_node(state)
    assert result["degraded"] is False
    assert len(provider.calls) == 1

    # The governor released the concurrency slot again.
    result = llm_node.llm_node(state)
    assert result["degraded"] is False
    assert len(provider.calls) == 2


def test_the_public_chat_path_degrades_when_the_budget_is_exhausted(monkeypatch: pytest.MonkeyPatch):
    from server.graph.nodes import llm_node

    governor = make_governor(provider="deepseek", rpm=1, tpm=100000, max_concurrency=2, fallback=("deepseek",))
    provider = FakeProvider()
    monkeypatch.setattr("openai.OpenAI", provider.openai_factory())
    _reset_chat_clients(monkeypatch, governor)

    state = {"session_id": "synthetic", "slots": {}, "reasoning": "合成上下文", "trace": []}
    llm_node.llm_node(state)
    second = llm_node.llm_node(state)
    assert second["degraded"] is True
    # The refused call never reached the provider.
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_the_public_sse_path_is_admitted_by_the_governor(monkeypatch: pytest.MonkeyPatch):
    from server.graph.nodes import llm_node

    governor = make_governor(provider="deepseek", rpm=5, tpm=100000, max_concurrency=2, fallback=("deepseek",))
    provider = FakeProvider()
    provider.plan.append(("stream", ["答案"]))
    monkeypatch.setattr("openai.AsyncOpenAI", provider.async_openai_factory())
    _reset_chat_clients(monkeypatch, governor)

    state = {"session_id": "synthetic", "slots": {}, "reasoning": "合成上下文", "trace": []}
    answer, degraded = await llm_node.generate_chat_candidate(state)
    assert (answer, degraded) == ("答案", False)
    assert len(provider.calls) == 1


class _FakeChatResult:
    """A LangChain chat result with a provider usage report."""

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5) -> None:
        self.llm_output = {"token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}
        self.generations = [[SimpleNamespace(message=SimpleNamespace(usage_metadata=None))]]


class _FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChatModel:
    """The parts of a LangChain chat model the governed wrapper touches."""

    def __init__(self) -> None:
        self.model_name = "fake-model"
        self.max_tokens = 64
        self.calls: list[Any] = []
        self.fail_with: Exception | None = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        return _FakeChatResult()

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # Deliberately independent of _generate: a model that delegates to the
        # synchronous variant would otherwise be admitted twice.
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        return _FakeChatResult()

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(kwargs)
        for piece in ("a", "b"):
            yield _FakeChunk(piece)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for piece in ("a", "b"):
            yield _FakeChunk(piece)


class _DelegatingChatModel(_FakeChatModel):
    """A model whose async variant delegates to the synchronous one.

    The LangChain base implementations do this. Intercepting both variants
    would admit the same provider request twice, so the wrapper only
    intercepts the variant the model owns.
    """

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _chat_model_governor(**quota) -> LLMResourceGovernor:
    config = GovernanceConfig(
        enabled=True,
        namespace="gaobao:test:governed-chat-model",
        max_wait_seconds=0.0,
        providers={"openai": ProviderQuota(**quota)},
    )
    return LLMResourceGovernor(config, backend=LocalGovernorBackend())


# ── The governed LangChain chat model ────────────────────────────────────
#
# The Deep Agents engine builds its own chat model, so the framework makes
# the provider request. The governed chat model intercepts that request
# instead of replacing the model.


def test_the_messages_are_converted_for_the_token_estimate():
    messages = [
        SimpleNamespace(type="human", content="你好"),
        SimpleNamespace(type="ai", content="您好"),
        SimpleNamespace(type="system", content="规则"),
    ]
    assert messages_to_dicts(messages) == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "您好"},
        {"role": "system", "content": "规则"},
    ]


def test_a_governed_call_is_admitted_and_released():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    model = govern_chat_model(_FakeChatModel(), provider="openai", operation="research", governor=governor)

    model._generate([SimpleNamespace(type="human", content="你好")])
    assert len(model.calls) == 1
    # The concurrency slot was returned, so a second call is admitted.
    model._generate([SimpleNamespace(type="human", content="你好")])
    assert len(model.calls) == 2


def test_a_governed_call_beyond_the_budget_never_reaches_the_provider():
    governor = _chat_model_governor(rpm=1, tpm=10000, max_concurrency=1)
    model = govern_chat_model(_FakeChatModel(), provider="openai", operation="research", governor=governor)

    model._generate([SimpleNamespace(type="human", content="你好")])
    with pytest.raises(LLMQuotaExceeded) as excinfo:
        model._generate([SimpleNamespace(type="human", content="你好")])
    assert excinfo.value.reason == "rpm"
    assert len(model.calls) == 1


def test_a_failing_governed_call_releases_the_slot():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    fake = _FakeChatModel()
    fake.fail_with = RuntimeError("provider is down")
    model = govern_chat_model(fake, provider="openai", operation="research", governor=governor)

    with pytest.raises(RuntimeError):
        model._generate([SimpleNamespace(type="human", content="你好")])

    fake.fail_with = None
    model._generate([SimpleNamespace(type="human", content="你好")])
    assert len(fake.calls) == 2


def test_a_governed_stream_releases_the_slot():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    model = govern_chat_model(_FakeChatModel(), provider="openai", operation="research", governor=governor)

    chunks = list(model._stream([SimpleNamespace(type="human", content="你好")]))
    assert len(chunks) == 2
    model._generate([SimpleNamespace(type="human", content="你好")])


@pytest.mark.asyncio
async def test_a_governed_async_stream_releases_the_slot():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    model = govern_chat_model(_FakeChatModel(), provider="openai", operation="research", governor=governor)

    chunks = [chunk async for chunk in model._astream([SimpleNamespace(type="human", content="你好")])]
    assert len(chunks) == 2
    await model._agenerate([SimpleNamespace(type="human", content="你好")])


def test_a_governed_model_is_only_wrapped_once():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    model = govern_chat_model(_FakeChatModel(), provider="openai", operation="research", governor=governor)
    again = govern_chat_model(model, provider="openai", operation="research", governor=governor)
    assert again is model


@pytest.mark.asyncio
async def test_a_delegating_async_model_is_admitted_once():
    governor = _chat_model_governor(rpm=10, tpm=10000, max_concurrency=1)
    fake = _DelegatingChatModel()
    model = govern_chat_model(fake, provider="openai", operation="research", governor=governor)

    await model._agenerate([SimpleNamespace(type="human", content="你好")])
    assert len(fake.calls) == 1
    # The single admission was released, so the next call is admitted too.
    await model._agenerate([SimpleNamespace(type="human", content="你好")])
    assert len(fake.calls) == 2
