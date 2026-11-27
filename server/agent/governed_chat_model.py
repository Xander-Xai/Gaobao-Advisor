"""Governed LangChain chat models.

The Deep Agents research engine builds its own LangChain chat model
(``ChatOpenAI``) and calls it through the LangGraph agent loop, so the
provider request never passes through the call sites this repository owns.
Instead of replacing the chat model — which would break the tool binding and
the model metadata the agent relies on — the *provider call* of the existing
model is intercepted:

* ``_generate`` / ``_agenerate`` — the non-streaming provider calls.
* ``_stream`` / ``_astream`` — the streaming provider calls.

Every intercepted call is admitted by the LLM Resource Governor, charged its
estimated input and reserved output, and reconciled with the real provider
usage when it finishes, fails or is cancelled.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from server.agent.llm_reliability import estimate_messages_tokens, estimate_tokens
from server.services.llm_governor import ResourceLease, get_governor

logger = logging.getLogger(__name__)

__all__ = ["govern_chat_model", "messages_to_dicts"]

_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "user",
    "function": "user",
    "chat": "user",
}


def messages_to_dicts(messages: Any) -> list[dict[str, str]]:
    """Convert LangChain messages into the plain dicts the estimator expects."""
    result: list[dict[str, str]] = []
    for message in messages or []:
        kind = getattr(message, "type", None)
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            content = str(content)
        role = _ROLE_MAP.get(str(kind), "user")
        result.append({"role": role, "content": content})
    return result


def _estimate_input(messages: Any) -> int:
    return estimate_messages_tokens(messages_to_dicts(messages))


def _output_reservation(model: Any, kwargs: dict[str, Any]) -> int:
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    value = getattr(model, "max_tokens", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return int(get_governor().config.default_output_reservation)


def _usage_of(result: Any) -> tuple[int, int]:
    """Read the provider usage out of a LangChain chat result."""
    llm_output = getattr(result, "llm_output", None)
    if isinstance(llm_output, dict):
        usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
            completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
            if isinstance(prompt, int) and isinstance(completion, int):
                return prompt, completion
    generations = getattr(result, "generations", None) or []
    for batch in generations:
        for generation in batch or []:
            metadata = getattr(generation, "message", None)
            usage = getattr(metadata, "usage_metadata", None)
            if isinstance(usage, dict):
                prompt = usage.get("input_tokens", 0)
                completion = usage.get("output_tokens", 0)
                if isinstance(prompt, int) and isinstance(completion, int):
                    return prompt, completion
    return 0, 0


def _acquire(
    governor: Any,
    provider: str,
    model_name: str,
    operation: str,
    input_tokens: int,
    output_tokens: int,
) -> ResourceLease:
    return governor.acquire(
        provider,
        model=model_name,
        operation=operation,
        estimated_input_tokens=input_tokens,
        reserved_output_tokens=output_tokens,
    )


def _settle(governor: Any, lease: ResourceLease, input_tokens: int, output_tokens: int) -> None:
    from server.metrics import record_governor_tokens

    counted = governor.reconcile(lease, input_tokens=input_tokens, output_tokens=output_tokens)
    record_governor_tokens(lease.provider, lease.model, reserved=lease.counted_reserved, used=counted)
    governor.release(lease)


#: The lease of the provider call that is currently in flight. A framework
#: model whose async variant delegates to the synchronous variant would
#: otherwise be admitted twice for one provider request. The context is per
#: task (async) and per thread (sync), so concurrent calls never share it.
_active_lease: ContextVar[Any] = ContextVar("gaobao_governed_lease", default=None)


def _owns_implementation(model: Any, name: str) -> bool:
    """True when the model class provides its own implementation of *name*."""
    implementation = getattr(type(model), name, None)
    if implementation is None:
        return False
    qualname = getattr(implementation, "__qualname__", "")
    # The LangChain base class implementations delegate to the synchronous
    # variant, which is already intercepted.
    return not qualname.startswith("BaseChatModel.")


def govern_chat_model(
    model: Any,
    *,
    provider: str = "openai",
    operation: str = "research",
    model_name: str = "",
    governor: Any = None,
) -> Any:
    """Return *model* with its provider calls routed through the governor.

    The chat model instance itself is kept, so tool binding, model metadata
    and every agent framework expectation stay exactly as they were.
    """
    if getattr(model, "_gaobao_governed", False):
        return model

    governor = governor or get_governor()
    resolved_name = model_name or str(getattr(model, "model_name", "") or getattr(model, "model", "") or "")

    original_generate = getattr(model, "_generate", None)
    original_agenerate = getattr(model, "_agenerate", None)
    original_stream = getattr(model, "_stream", None)
    original_astream = getattr(model, "_astream", None)

    # A framework default implementation delegates to the synchronous variant.
    # Patching both would admit the same provider request twice, so only the
    # variant the model actually owns is intercepted.
    if not _owns_implementation(model, "_agenerate"):
        original_agenerate = None
    if not _owns_implementation(model, "_astream"):
        original_astream = None

    if original_generate is not None:

        def _generate(messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            if _active_lease.get() is not None:
                # A nested call of the same provider request: the outer lease
                # already accounts for it.
                return original_generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            lease = _acquire(
                governor,
                provider,
                resolved_name,
                operation,
                _estimate_input(messages),
                _output_reservation(model, kwargs),
            )
            token = _active_lease.set(lease)
            try:
                result = original_generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except BaseException:
                governor.reconcile(lease)
                governor.release(lease)
                raise
            finally:
                _active_lease.reset(token)
            used_in, used_out = _usage_of(result)
            _settle(governor, lease, used_in, used_out)
            return result

        model._generate = _generate

    if original_agenerate is not None:

        async def _agenerate(messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            import asyncio

            if _active_lease.get() is not None:
                return await original_agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            lease = await asyncio.to_thread(
                _acquire,
                governor,
                provider,
                resolved_name,
                operation,
                _estimate_input(messages),
                _output_reservation(model, kwargs),
            )
            token = _active_lease.set(lease)
            try:
                result = await original_agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except BaseException:
                governor.reconcile(lease)
                governor.release(lease)
                raise
            finally:
                _active_lease.reset(token)
            used_in, used_out = _usage_of(result)
            _settle(governor, lease, used_in, used_out)
            return result

        model._agenerate = _agenerate

    if original_stream is not None:

        def _stream(messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            if _active_lease.get() is not None:
                return original_stream(messages, stop=stop, run_manager=run_manager, **kwargs)
            lease = _acquire(
                governor,
                provider,
                resolved_name,
                operation,
                _estimate_input(messages),
                _output_reservation(model, kwargs),
            )
            chunks: list[str] = []

            def _iterator():
                token = _active_lease.set(lease)
                try:
                    for chunk in original_stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                        text = getattr(chunk, "content", None)
                        if isinstance(text, str):
                            chunks.append(text)
                        governor.renew(lease)
                        yield chunk
                finally:
                    _active_lease.reset(token)
                    _settle(governor, lease, 0, estimate_tokens("".join(chunks)))

            return _iterator()

        model._stream = _stream

    if original_astream is not None:

        async def _astream(messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            # An async generator function: LangChain iterates the result of
            # ``_astream`` directly, without awaiting it first.
            import asyncio

            if _active_lease.get() is not None:
                async for chunk in original_astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield chunk
                return
            lease = await asyncio.to_thread(
                _acquire,
                governor,
                provider,
                resolved_name,
                operation,
                _estimate_input(messages),
                _output_reservation(model, kwargs),
            )
            chunks: list[str] = []
            token = _active_lease.set(lease)
            try:
                async for chunk in original_astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    text = getattr(chunk, "content", None)
                    if isinstance(text, str):
                        chunks.append(text)
                    governor.renew(lease)
                    yield chunk
            finally:
                _active_lease.reset(token)
                _settle(governor, lease, 0, estimate_tokens("".join(chunks)))

        model._astream = _astream

    try:
        model._gaobao_governed = True
    except Exception:  # pragma: no cover - some models forbid attribute writes
        logger.debug("Could not mark the chat model as governed")
    return model
