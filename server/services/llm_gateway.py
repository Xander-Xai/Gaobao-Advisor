"""Governed model access — the only place a provider request may leave the process.

Every internal model call in this repository goes through this module. The
module owns four responsibilities that used to be scattered across call sites
and therefore easy to bypass:

1. **Admission** — every attempt, including every retry and every provider
   fallback, is admitted by :mod:`server.services.llm_governor`.
2. **Accounting** — the estimated input and the reserved output are charged
   before the call and reconciled with the real provider usage afterwards.
3. **Release** — a streaming call releases its concurrency slot when it ends,
   fails or is cancelled.
4. **Degradation** — a provider 429 is answered with the provider's own
   ``Retry-After`` (or rate-limit reset header), a bounded backoff and, when
   the provider stays unavailable, the configured fallback chain.

The clients keep the OpenAI SDK surface (``client.chat.completions.create``,
``client.embeddings.create``) so a call site changes by one import, and so a
test can inject a fake client at the same seam.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any

from server.agent.llm_reliability import estimate_messages_tokens, estimate_tokens
from server.services.llm_governor import (
    LLMQuotaExceeded,
    LLMResourceGovernor,
    ResourceLease,
    get_governor,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AsyncGovernedClient",
    "GovernedClient",
    "ProviderNotConfigured",
    "ProviderRateLimited",
    "governed_async_client",
    "governed_client",
    "raw_provider_client",
    "reset_clients",
]

#: Bounded operation names. They become Prometheus labels, so they must stay finite.
OPERATIONS = frozenset(
    {
        "chat",
        "summary",
        "judge",
        "repair",
        "embedding",
        "voice",
        "research",
        "planner",
        "sandbox",
    }
)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_DEFAULT_TIMEOUT = 120.0


class ProviderNotConfigured(RuntimeError):
    """A provider has no usable endpoint or credential."""


class ProviderRateLimited(RuntimeError):
    """The provider answered 429."""

    def __init__(self, message: str, *, retry_after: float = 0.0, provider: str = "") -> None:
        super().__init__(message)
        self.retry_after = max(0.0, retry_after)
        self.provider = provider


# ── Retry and rate-limit helpers ───────────────────────────────────────────


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _parse_duration(value: str | None) -> float | None:
    """Parse ``1.5s`` / ``6m0s`` / ``250ms`` / ``30`` into seconds."""
    if not value:
        return None
    text = str(value).strip().lower()
    if text.isdigit():
        return float(text)
    total = 0.0
    matched = False
    number = ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
            continue
        if not number:
            continue
        matched = True
        try:
            amount = float(number)
        except ValueError:
            amount = 0.0
        number = ""
        if char == "ms":
            total += amount / 1000.0
        elif char == "s":
            total += amount
        elif char == "m":
            total += amount * 60
        elif char == "h":
            total += amount * 3600
    if number:
        try:
            total += float(number)
        except ValueError:
            return None
    return total if matched or total else None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Read the provider's own backoff hint, if it exposes one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        try:
            raw = getter(header)
        except Exception:  # pragma: no cover - defensive
            raw = None
        parsed = _parse_duration(raw)
        if parsed is not None:
            return parsed
    return None


def _usage_of(response: Any) -> tuple[int, int]:
    """Extract ``(input_tokens, output_tokens)`` from an OpenAI-compatible response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0

    def _read(*names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        return 0

    return _read("prompt_tokens", "input_tokens"), _read("completion_tokens", "output_tokens")


def _estimate_input_tokens(kwargs: dict[str, Any]) -> int:
    """Estimate the prompt side of a request without ever reading user content."""
    messages = kwargs.get("messages")
    if isinstance(messages, list) and messages:
        return estimate_messages_tokens(messages)
    payload = kwargs.get("input")
    if isinstance(payload, str):
        return estimate_tokens(payload)
    if isinstance(payload, (list, tuple)):
        total = 0
        for item in payload:
            if isinstance(item, str):
                total += estimate_tokens(item)
            elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                total += estimate_tokens(item[0])
        return total
    prompt = kwargs.get("prompt")
    if isinstance(prompt, str):
        return estimate_tokens(prompt)
    return 0


def _output_reservation(kwargs: dict[str, Any]) -> int:
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return int(get_governor().config.default_output_reservation)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ── Provider client factory ────────────────────────────────────────────────


class _ClientFactory:
    """Creates and caches one raw SDK client per provider."""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_retries: int = 0,
        extra: dict[str, Any] | None = None,
        config_override: dict[str, Any] | None = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra = dict(extra or {})
        self.config_override = dict(config_override or {})
        self._clients: dict[str, Any] = {}
        self._async_clients: dict[str, Any] = {}

    def config_for(self, provider: str) -> dict[str, Any]:
        from config.loader import load_provider_config

        cfg = dict(load_provider_config(provider))
        override = self.config_override.get(provider)
        if override:
            cfg.update({key: value for key, value in override.items() if value})
        return cfg

    def client_for(self, provider: str) -> Any:
        if provider in self._clients:
            return self._clients[provider]
        from openai import OpenAI

        client = OpenAI(**self._client_kwargs(provider))
        self._clients[provider] = client
        return client

    def async_client_for(self, provider: str) -> Any:
        if provider in self._async_clients:
            return self._async_clients[provider]
        from openai import AsyncOpenAI

        client = AsyncOpenAI(**self._client_kwargs(provider))
        self._async_clients[provider] = client
        return client

    def _client_kwargs(self, provider: str) -> dict[str, Any]:
        cfg = self.config_for(provider)
        api_key = str(cfg.get("api_key") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip()
        if not api_key:
            raise ProviderNotConfigured(f"provider {provider} has no API key")
        if not base_url:
            raise ProviderNotConfigured(f"provider {provider} has no base URL")
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": self.timeout if self.timeout is not None else float(cfg.get("timeout") or _DEFAULT_TIMEOUT),
            # Retries are governed by this module so that every retry passes
            # the governor again. The SDK must not retry behind our back.
            "max_retries": self.max_retries,
        }
        kwargs.update(self.extra)
        return kwargs

    def reset(self) -> None:
        for client in list(self._clients.values()) + list(self._async_clients.values()):
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._clients.clear()
        self._async_clients.clear()


_factories: list[_ClientFactory] = []


def reset_clients() -> None:
    """Close and forget every cached provider client (tests, config reloads)."""
    for factory in _factories:
        factory.reset()
    _factories.clear()


def raw_provider_client(provider: str, *, timeout: float | None = None, max_retries: int = 0) -> Any:
    """Return an *ungoverned* SDK client.

    Only for code paths that already own their own admission decision (there
    are none in production today) and for tests. Production call sites must
    use :func:`governed_client`.
    """
    factory = _ClientFactory(timeout=timeout, max_retries=max_retries)
    _factories.append(factory)
    return factory.client_for(provider)


# ── Shared call logic ──────────────────────────────────────────────────────


class _GovernedCall:
    """Provider independent admission, retry, fallback and accounting."""

    def __init__(
        self,
        *,
        factory: _ClientFactory,
        operation: str,
        provider: str | None,
        model: str | None,
        governor: LLMResourceGovernor | None = None,
    ):
        self.factory = factory
        self.operation = operation if operation in OPERATIONS else "chat"
        self.requested_provider = provider
        self.requested_model = model
        self.governor: LLMResourceGovernor = governor or get_governor()

    # -- provider chain ---------------------------------------------------
    def primary_provider(self) -> str:
        if self.requested_provider:
            return self.requested_provider
        from config.loader import primary_provider_name

        return primary_provider_name()

    def provider_chain(self) -> list[str]:
        primary = self.primary_provider()
        chain = [primary]
        for candidate in self.governor.fallback_chain(primary):
            if candidate not in chain:
                chain.append(candidate)
        return chain

    def model_for(self, provider: str, kwargs: dict[str, Any]) -> str:
        explicit = kwargs.get("model")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        if self.requested_model:
            return self.requested_model
        try:
            cfg = self.factory.config_for(provider)
        except Exception:
            cfg = {}
        return str(cfg.get("model") or "").strip()

    # -- admission ---------------------------------------------------------
    def acquire(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> ResourceLease:
        """Admit one attempt, waiting for the quota when the policy allows it."""
        deadline = time.monotonic() + max(0.0, self.governor.config.max_wait_seconds)
        started = time.monotonic()
        while True:
            try:
                lease = self.governor.acquire(
                    provider,
                    model=model,
                    operation=self.operation,
                    estimated_input_tokens=input_tokens,
                    reserved_output_tokens=output_tokens,
                )
            except LLMQuotaExceeded as exc:
                from server.metrics import record_governor_rejection

                record_governor_rejection(provider, exc.reason)
                if exc.reason == "redis_unavailable":
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(max(0.05, exc.retry_after), remaining))
                continue
            waited = time.monotonic() - started
            from server.metrics import record_governor_admission, record_governor_wait

            record_governor_admission(provider, model, self.operation)
            record_governor_wait(provider, waited)
            return lease

    async def acquire_async(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> ResourceLease:
        """Async twin of :meth:`acquire`.

        The admission runs in a worker thread, because the shared Redis
        backend is a synchronous client. The thread is shielded from the
        cancellation of the awaiting task: a cancelled request must not leak
        the concurrency slot it was granted a moment earlier.
        """
        deadline = time.monotonic() + max(0.0, self.governor.config.max_wait_seconds)
        started = time.monotonic()
        while True:
            try:
                lease = await self._admit_in_thread(provider, model, input_tokens, output_tokens)
            except LLMQuotaExceeded as exc:
                from server.metrics import record_governor_rejection

                record_governor_rejection(provider, exc.reason)
                if exc.reason == "redis_unavailable":
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                await asyncio.sleep(min(max(0.05, exc.retry_after), remaining))
                continue
            waited = time.monotonic() - started
            from server.metrics import record_governor_admission, record_governor_wait

            record_governor_admission(provider, model, self.operation)
            record_governor_wait(provider, waited)
            return lease

    async def _admit_in_thread(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> ResourceLease:
        """Admit in a worker thread without losing a granted slot on cancel."""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            None,
            lambda: self.governor.acquire(
                provider,
                model=model,
                operation=self.operation,
                estimated_input_tokens=input_tokens,
                reserved_output_tokens=output_tokens,
            ),
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # The admission may already have been granted in the worker
            # thread. Return the slot as soon as the thread finishes.
            def _release_granted(done: Any) -> None:
                if done.cancelled() or done.exception() is not None:
                    return
                lease = done.result()
                if lease is not None:
                    self.governor.reconcile(lease)
                    self.governor.release(lease)

            future.add_done_callback(_release_granted)
            raise

    # -- error handling ----------------------------------------------------
    def handle_failure(
        self,
        exc: BaseException,
        *,
        provider: str,
        attempt: int,
        attempts: int,
    ) -> tuple[bool, float]:
        """Return ``(retryable, delay_seconds)`` for one failed attempt."""
        status = _status_of(exc)
        if status == 429:
            from server.metrics import record_provider_429

            record_provider_429(provider)
        if status not in _RETRYABLE_STATUS or attempt >= attempts:
            return False, 0.0
        delay = self.backoff(attempt)
        hinted = _retry_after_seconds(exc)
        if hinted is not None:
            delay = min(hinted, self.max_retry_after())
        return True, delay

    def backoff(self, attempt: int) -> float:
        base = _env_float("GAOBAO_LLM_GOVERNOR_RETRY_BASE_DELAY", 1.0)
        delay = base * (2**attempt)
        jitter = random.uniform(0, min(0.25, delay * 0.1))
        return min(delay + jitter, self.max_retry_after())

    def max_retry_after(self) -> float:
        return _env_float("GAOBAO_LLM_GOVERNOR_MAX_RETRY_AFTER", 30.0)

    def attempts(self) -> int:
        return max(1, int(_env_float("GAOBAO_LLM_GOVERNOR_MAX_ATTEMPTS", 2.0)))

    # -- accounting --------------------------------------------------------
    def settle(self, lease: ResourceLease, *, input_tokens: int, output_tokens: int) -> None:
        from server.metrics import record_governor_tokens

        counted = self.governor.reconcile(lease, input_tokens=input_tokens, output_tokens=output_tokens)
        record_governor_tokens(lease.provider, lease.model, reserved=lease.counted_reserved, used=counted)
        self.governor.release(lease)

    def abandon(self, lease: ResourceLease) -> None:
        """Release a lease whose call failed before it produced any usage."""
        self.governor.reconcile(lease)
        self.governor.release(lease)


# ── Synchronous client ─────────────────────────────────────────────────────


class GovernedClient:
    """An OpenAI SDK shaped client that passes every request through the governor."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        operation: str = "chat",
        timeout: float | None = None,
        max_retries: int = 0,
        config_override: dict[str, Any] | None = None,
        factory: _ClientFactory | None = None,
        governor: LLMResourceGovernor | None = None,
    ) -> None:
        self._factory = factory or _ClientFactory(
            timeout=timeout,
            max_retries=max_retries,
            config_override=config_override,
        )
        _factories.append(self._factory)
        self._call = _GovernedCall(
            factory=self._factory,
            operation=operation,
            provider=provider,
            model=model,
            governor=governor,
        )
        self.chat = _GovernedChat(self._call)
        self.embeddings = _GovernedEmbeddings(self._call)

    @property
    def provider(self) -> str:
        return self._call.primary_provider()

    def close(self) -> None:
        self._factory.reset()

    def __enter__(self) -> GovernedClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class _GovernedChat:
    def __init__(self, call: _GovernedCall) -> None:
        self.completions = _GovernedCompletions(call)


class _GovernedCompletions:
    def __init__(self, call: _GovernedCall) -> None:
        self._call = call

    def create(self, **kwargs: Any) -> Any:
        call = self._call
        input_tokens = _estimate_input_tokens(kwargs)
        output_reservation = _output_reservation(kwargs)
        attempts = call.attempts()
        failure: BaseException | None = None
        quota_error: BaseException | None = None
        unconfigured: BaseException | None = None

        for provider in call.provider_chain():
            model = call.model_for(provider, kwargs)
            request_kwargs = dict(kwargs)
            request_kwargs["model"] = model
            try:
                client = call.factory.client_for(provider)
            except ProviderNotConfigured as exc:
                # A provider without an endpoint or a credential is skipped
                # before the admission, so it does not consume the budget.
                unconfigured = unconfigured or exc
                break
            for attempt in range(attempts):
                try:
                    lease = call.acquire(provider, model, input_tokens, output_reservation)
                except LLMQuotaExceeded as exc:
                    # The provider's own budget is exhausted. The call is not
                    # made here; it degrades to the next provider of the chain,
                    # which has its own budget and is governed as well.
                    if exc.reason == "redis_unavailable":
                        raise
                    quota_error = quota_error or exc
                    break
                try:
                    response = client.chat.completions.create(**request_kwargs)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    call.abandon(lease)
                    retryable, delay = call.handle_failure(
                        exc,
                        provider=provider,
                        attempt=attempt,
                        attempts=attempts,
                    )
                    if not retryable:
                        raise
                    from server.metrics import record_governor_retry

                    record_governor_retry(provider, str(_status_of(exc)))
                    failure = failure or exc
                    time.sleep(delay)
                    continue
                if kwargs.get("stream"):
                    return _governed_stream(response, call, lease)
                used_in, used_out = _usage_of(response)
                call.settle(lease, input_tokens=used_in, output_tokens=used_out)
                return response
            from server.metrics import record_governor_fallback

            record_governor_fallback(provider)
        if quota_error is not None:
            raise quota_error
        if failure is not None:
            raise failure
        if unconfigured is not None:
            raise unconfigured
        raise ProviderNotConfigured("no provider is configured for this call")


class _GovernedEmbeddings:
    def __init__(self, call: _GovernedCall) -> None:
        self._call = call

    def create(self, **kwargs: Any) -> Any:
        call = self._call
        input_tokens = _estimate_input_tokens(kwargs)
        attempts = call.attempts()
        failure: BaseException | None = None
        quota_error: BaseException | None = None
        unconfigured: BaseException | None = None
        for provider in call.provider_chain():
            model = call.model_for(provider, kwargs)
            request_kwargs = dict(kwargs)
            request_kwargs["model"] = model
            try:
                client = call.factory.client_for(provider)
            except ProviderNotConfigured as exc:
                unconfigured = unconfigured or exc
                break
            for attempt in range(attempts):
                try:
                    lease = call.acquire(provider, model, input_tokens, 0)
                except LLMQuotaExceeded as exc:
                    if exc.reason == "redis_unavailable":
                        raise
                    quota_error = quota_error or exc
                    break
                try:
                    response = client.embeddings.create(**request_kwargs)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    call.abandon(lease)
                    retryable, delay = call.handle_failure(
                        exc,
                        provider=provider,
                        attempt=attempt,
                        attempts=attempts,
                    )
                    if not retryable:
                        raise
                    failure = failure or exc
                    time.sleep(delay)
                    continue
                used_in, _ = _usage_of(response)
                call.settle(lease, input_tokens=used_in, output_tokens=0)
                return response
        if quota_error is not None:
            raise quota_error
        if failure is not None:
            raise failure
        if unconfigured is not None:
            raise unconfigured
        raise ProviderNotConfigured("no provider is configured for this call")


class _GovernedSyncStream:
    """A sync provider stream whose concurrency slot is always released.

    The slot is returned when the stream ends, when it fails, when the
    consumer closes it and when the object is finalised. The produced text is
    used to reconcile the token reservation.
    """

    def __init__(self, source: Any, call: _GovernedCall, lease: ResourceLease) -> None:
        self._source = source
        self._call = call
        self._lease = lease
        self._chunks: list[str] = []
        self._settled = False

    def __iter__(self) -> _GovernedSyncStream:
        if not hasattr(self, "_iterator"):
            self._iterator = iter(self._source)
        return self

    def __next__(self) -> Any:
        if not hasattr(self, "_iterator"):
            self._iterator = iter(self._source)
        try:
            chunk = next(self._iterator)
        except StopIteration:
            self._settle()
            raise
        except BaseException:
            self._settle()
            raise
        self._absorb(chunk)
        return chunk

    def _absorb(self, chunk: Any) -> None:
        for choice in getattr(chunk, "choices", None) or []:
            piece = getattr(getattr(choice, "delta", None), "content", None)
            if isinstance(piece, str):
                self._chunks.append(piece)
        self._call.governor.renew(self._lease)

    def _settle(self) -> None:
        if self._settled:
            return
        self._settled = True
        self._call.settle(
            self._lease,
            input_tokens=0,
            output_tokens=estimate_tokens("".join(self._chunks)),
        )

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._settle()

    def __del__(self) -> None:  # pragma: no cover - interpreter dependent
        try:
            self._settle()
        except Exception:
            pass


def _governed_stream(source: Any, call: _GovernedCall, lease: ResourceLease) -> _GovernedSyncStream:
    """Wrap a sync provider stream so the slot is always released."""
    return _GovernedSyncStream(source, call, lease)


# ── Asynchronous client ────────────────────────────────────────────────────


class AsyncGovernedClient:
    """The async twin of :class:`GovernedClient`."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        operation: str = "chat",
        timeout: float | None = None,
        max_retries: int = 0,
        config_override: dict[str, Any] | None = None,
        factory: _ClientFactory | None = None,
        governor: LLMResourceGovernor | None = None,
    ) -> None:
        self._factory = factory or _ClientFactory(
            timeout=timeout,
            max_retries=max_retries,
            config_override=config_override,
        )
        _factories.append(self._factory)
        self._call = _GovernedCall(
            factory=self._factory,
            operation=operation,
            provider=provider,
            model=model,
            governor=governor,
        )
        self.chat = _AsyncGovernedChat(self._call)
        self.embeddings = _AsyncGovernedEmbeddings(self._call)

    @property
    def provider(self) -> str:
        return self._call.primary_provider()

    async def close(self) -> None:
        self._factory.reset()

    async def __aenter__(self) -> AsyncGovernedClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class _AsyncGovernedChat:
    def __init__(self, call: _GovernedCall) -> None:
        self.completions = _AsyncGovernedCompletions(call)


class _AsyncGovernedCompletions:
    def __init__(self, call: _GovernedCall) -> None:
        self._call = call

    async def create(self, **kwargs: Any) -> Any:
        call = self._call
        input_tokens = _estimate_input_tokens(kwargs)
        output_reservation = _output_reservation(kwargs)
        attempts = call.attempts()
        failure: BaseException | None = None
        quota_error: BaseException | None = None
        unconfigured: BaseException | None = None

        for provider in call.provider_chain():
            model = call.model_for(provider, kwargs)
            request_kwargs = dict(kwargs)
            request_kwargs["model"] = model
            try:
                client = await asyncio.to_thread(call.factory.async_client_for, provider)
            except ProviderNotConfigured as exc:
                # A provider without an endpoint or a credential is skipped
                # before the admission, so it does not consume the budget.
                unconfigured = unconfigured or exc
                break
            for attempt in range(attempts):
                try:
                    lease = await call.acquire_async(provider, model, input_tokens, output_reservation)
                except LLMQuotaExceeded as exc:
                    if exc.reason == "redis_unavailable":
                        raise
                    quota_error = quota_error or exc
                    break
                try:
                    response = await client.chat.completions.create(**request_kwargs)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    call.abandon(lease)
                    retryable, delay = call.handle_failure(
                        exc,
                        provider=provider,
                        attempt=attempt,
                        attempts=attempts,
                    )
                    if not retryable:
                        raise
                    from server.metrics import record_governor_retry

                    record_governor_retry(provider, str(_status_of(exc)))
                    failure = failure or exc
                    await asyncio.sleep(delay)
                    continue
                if kwargs.get("stream"):
                    return _async_governed_stream(response, call, lease)
                used_in, used_out = _usage_of(response)
                call.settle(lease, input_tokens=used_in, output_tokens=used_out)
                return response
            from server.metrics import record_governor_fallback

            record_governor_fallback(provider)
        if quota_error is not None:
            raise quota_error
        if failure is not None:
            raise failure
        if unconfigured is not None:
            raise unconfigured
        raise ProviderNotConfigured("no provider is configured for this call")


class _AsyncGovernedEmbeddings:
    def __init__(self, call: _GovernedCall) -> None:
        self._call = call

    async def create(self, **kwargs: Any) -> Any:
        call = self._call
        input_tokens = _estimate_input_tokens(kwargs)
        attempts = call.attempts()
        failure: BaseException | None = None
        quota_error: BaseException | None = None
        unconfigured: BaseException | None = None
        for provider in call.provider_chain():
            model = call.model_for(provider, kwargs)
            request_kwargs = dict(kwargs)
            request_kwargs["model"] = model
            try:
                client = await asyncio.to_thread(call.factory.async_client_for, provider)
            except ProviderNotConfigured as exc:
                unconfigured = unconfigured or exc
                break
            for attempt in range(attempts):
                try:
                    lease = await call.acquire_async(provider, model, input_tokens, 0)
                except LLMQuotaExceeded as exc:
                    if exc.reason == "redis_unavailable":
                        raise
                    quota_error = quota_error or exc
                    break
                try:
                    response = await client.embeddings.create(**request_kwargs)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    call.abandon(lease)
                    retryable, delay = call.handle_failure(
                        exc,
                        provider=provider,
                        attempt=attempt,
                        attempts=attempts,
                    )
                    if not retryable:
                        raise
                    failure = failure or exc
                    await asyncio.sleep(delay)
                    continue
                used_in, _ = _usage_of(response)
                call.settle(lease, input_tokens=used_in, output_tokens=0)
                return response
        if quota_error is not None:
            raise quota_error
        if failure is not None:
            raise failure
        if unconfigured is not None:
            raise unconfigured
        raise ProviderNotConfigured("no provider is configured for this call")


class _GovernedAsyncStream:
    """An async provider stream whose concurrency slot is always released.

    A cancelled consumer task does not close an async generator on its own,
    so the slot is also returned when the consuming task finishes. Without
    that, a cancelled request would keep its provider slot until the lease
    expires.
    """

    def __init__(self, source: Any, call: _GovernedCall, lease: ResourceLease) -> None:
        self._source = source
        self._call = call
        self._lease = lease
        self._chunks: list[str] = []
        self._settled = False
        self._register_task_release()

    def _register_task_release(self) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:  # pragma: no cover - no running loop
            return
        if task is None:
            return

        def _release() -> None:
            self._settle()

        task.add_done_callback(_release)

    def __aiter__(self) -> _GovernedAsyncStream:
        self._iterator = self._source.__aiter__()
        return self

    async def __anext__(self) -> Any:
        iterator = getattr(self, "_iterator", None)
        if iterator is None:
            iterator = self._iterator = self._source.__aiter__()
        try:
            chunk = await iterator.__anext__()
        except StopAsyncIteration:
            self._settle()
            raise
        except BaseException:
            self._settle()
            raise
        for choice in getattr(chunk, "choices", None) or []:
            piece = getattr(getattr(choice, "delta", None), "content", None)
            if isinstance(piece, str):
                self._chunks.append(piece)
        self._call.governor.renew(self._lease)
        return chunk

    def _settle(self) -> None:
        if self._settled:
            return
        self._settled = True
        self._call.settle(
            self._lease,
            input_tokens=0,
            output_tokens=estimate_tokens("".join(self._chunks)),
        )

    async def aclose(self) -> None:
        close = getattr(self._source, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._settle()


def _async_governed_stream(source: Any, call: _GovernedCall, lease: ResourceLease) -> _GovernedAsyncStream:
    """Wrap an async provider stream so the slot is released on end or cancel."""
    return _GovernedAsyncStream(source, call, lease)


# ── Factories ──────────────────────────────────────────────────────────────


def governed_client(
    provider: str | None = None,
    *,
    model: str | None = None,
    operation: str = "chat",
    timeout: float | None = None,
    max_retries: int = 0,
    config_override: dict[str, Any] | None = None,
    governor: LLMResourceGovernor | None = None,
) -> GovernedClient:
    """Return a governed synchronous client.

    ``config_override`` supplies the endpoint and the credential of one
    provider explicitly. It is used by the embedding and voice paths, which
    keep their own credential namespace (``OPENAI_API_KEY``,
    ``DASHSCOPE_API_KEY``, …) instead of the shared ``LLM_API_KEY``.
    """
    return GovernedClient(
        provider=provider,
        model=model,
        operation=operation,
        timeout=timeout,
        max_retries=max_retries,
        config_override=config_override,
        governor=governor,
    )


def governed_openai_client(
    provider: str = "openai",
    *,
    operation: str = "planner",
    timeout: float | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> GovernedClient:
    """Governed client for an OpenAI-compatible endpoint with its own credentials.

    The Deep Agents worker, the sandbox analyser and the offline benchmarks
    keep their own ``OPENAI_API_KEY`` / ``OPENAI_API_BASE`` namespace instead
    of the shared ``LLM_API_KEY``. They still have to obey the provider
    budget, so they use this helper rather than a raw SDK client.
    """
    override = {
        "api_key": api_key if api_key is not None else os.getenv("OPENAI_API_KEY", ""),
        "base_url": base_url if base_url is not None else os.getenv("OPENAI_API_BASE", ""),
        "model": model or "",
    }
    return governed_client(
        provider,
        model=model,
        operation=operation,
        timeout=timeout,
        config_override={provider: override},
    )


def governed_async_client(
    provider: str | None = None,
    *,
    model: str | None = None,
    operation: str = "chat",
    timeout: float | None = None,
    max_retries: int = 0,
    config_override: dict[str, Any] | None = None,
    governor: LLMResourceGovernor | None = None,
) -> AsyncGovernedClient:
    """Return a governed asynchronous client."""
    return AsyncGovernedClient(
        provider=provider,
        model=model,
        operation=operation,
        timeout=timeout,
        max_retries=max_retries,
        config_override=config_override,
        governor=governor,
    )
