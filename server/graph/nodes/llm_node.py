"""LLM reasoning node — calls the LLM to generate a conversational reply.

This module provides both synchronous (graph node) and streaming (SSE) entry
points with automatic retry, context trimming, token estimation, and
conversation memory management (dual-layer: recent + summary).

Migration notes:
- retry_api_call     ← backend/agent/core.py:31-51
- trim_messages      ← backend/agent/core.py:54-69
- estimate_tokens    ← backend/agent/langchain_agent.py:37-39
- MemoryManager      ← backend/agent/langchain_agent.py:106-149
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Generator
from typing import Any

from openai import AsyncOpenAI, OpenAI

from config.loader import load_llm_config
from server.agent.chat_context import build_chat_context, build_compacted_chat_context
from server.agent.llm_reliability import (
    build_llm_context,
    estimate_messages_tokens,
)

MODEL = "gpt-4o"  # Default model if config fails
logger = logging.getLogger(__name__)


# ── Retry constants (sync variant) ─────────────────────────────────
MAX_RETRIES = 2
RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
BASE_DELAY = 1.0
MAX_HISTORY_ROUNDS = 20

# ── Client (lazy singleton) ────────────────────────────────────────
_client: OpenAI | None = None
_client_lock = threading.Lock()
_config: dict | None = None
_FALLBACK_REPLY = "抱歉，我现在暂时无法给出完整分析。请稍后再试，或者告诉我你的省份和分数，我帮你做个初步判断。"


def _get_config() -> dict:
    global _config
    if _config is None:
        _config = load_llm_config()
    return _config


def _get_llm_client() -> OpenAI:
    global _client
    cfg = _get_config()
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = cfg["api_key"]
                if not api_key:
                    raise RuntimeError("LLM_API_KEY must be set (via config/llm_providers.yaml or env)")
                _client = OpenAI(
                    api_key=api_key,
                    base_url=cfg["base_url"],
                    timeout=cfg.get("timeout", 120.0),
                    max_retries=2,
                )
    return _client


def _load_system_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "system_prompt.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "你是一个资深高考志愿规划师。"


# ── Sync retry (for thread-pool / synchronous graph) ──────────────


def _sync_retry(coro_factory, max_retries=MAX_RETRIES, base_delay=BASE_DELAY):
    """Sync retry wrapper using time.sleep (for use in thread pool)."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return coro_factory()
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status in RETRYABLE_STATUS_CODES and attempt < max_retries:
                delay = base_delay * (2**attempt)
                logger.warning(
                    "LLM call failed (status=%s), retry %d/%d in %.1fs...",
                    status,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
                last_exc = e
            else:
                raise
    raise last_exc


# ── Graph node (sync, runs inside asyncio.to_thread) ──────────────


def llm_node(state: dict[str, Any]) -> dict[str, Any]:
    """Call the LLM with retry + context trimming + memory (non-streaming).

    Loads conversation history from the database via MemoryManager,
    applies dual-layer memory (recent verbatim + older summarized),
    trims if needed, then calls the LLM with retry.

    Runs synchronously — called from LangGraph.invoke() which is offloaded
    to a thread pool by the SSE handler.
    """
    cfg = _get_config()
    trace = list(state.get("trace", []))
    session_id = state.get("session_id", "")

    try:
        client = _get_llm_client()
        system_prompt = _load_system_prompt()

        # The legacy synchronous graph path uses the same bounded policy,
        # without implicitly paying for an additional summary-model call.
        context = _build_chat_context_sync(state, system_prompt)
        messages = context.messages
        trace.append(
            {
                "node": "llm_reason",
                "event": "memory_loaded",
                "session_id_present": bool(session_id),
                "context_status": context.status,
                "history_messages_omitted": context.omitted_messages,
            }
        )

        token_count = estimate_messages_tokens(messages)
        trace.append(
            {
                "node": "llm_reason",
                "event": "llm_call_start",
                "messages": len(messages),
                "estimated_tokens": token_count,
            }
        )

        response = _sync_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                temperature=cfg.get("temperature", 0.7),
                max_tokens=cfg.get("max_tokens") or 2000,
            )
        )
        content = response.choices[0].message.content
        reply = (content or "").strip()
        if not reply:
            reply = _FALLBACK_REPLY
    except Exception as exc:
        logger.warning("LLM call failed after retries: %s", exc)
        reply = _FALLBACK_REPLY
        trace.append(
            {
                "node": "llm_reason",
                "event": "llm_error",
                "error": str(exc)[:200],
            }
        )
        return {"reply": reply, "trace": trace, "degraded": True}

    trace.append({"node": "llm_reason", "event": "llm_reply_generated"})
    return {"reply": reply, "trace": trace, "degraded": False}


# ── Memory loading helper ─────────────────────────────────────────


def _load_memory_for_session(session_id: str) -> list[dict]:
    """Load conversation history from the database.

    Returns list of dicts with 'role' and 'content' keys.
    """
    if not session_id:
        return []
    from db.crud import load_conversation_history
    from db.database import get_session

    db = get_session()
    try:
        return load_conversation_history(db, session_id)
    except Exception:
        # Never silently turn a failed history read into a seemingly complete
        # new conversation; the caller must use the grounded fallback.
        raise RuntimeError("Conversation history unavailable") from None
    finally:
        db.close()


def _chat_context_options(*, compaction_enabled: bool = False) -> dict[str, int]:
    # Preserve the legacy 20-round window unless semantic compaction is
    # explicitly enabled. In compact mode keep a tighter 10-round tail.
    default_rounds = "10" if compaction_enabled else str(MAX_HISTORY_ROUNDS)
    return {
        "keep_recent_rounds": int(os.getenv("GAOBAO_CHAT_KEEP_RECENT_ROUNDS", default_rounds)),
        "max_estimated_tokens": int(os.getenv("GAOBAO_CHAT_INPUT_EST_TOKENS", "8000")),
    }


def _build_chat_context_sync(state: dict[str, Any], system_prompt: str):
    history = _load_memory_for_session(state.get("session_id", ""))
    return build_chat_context(history, system_prompt, build_llm_context(state), **_chat_context_options())


async def _build_chat_context_async(state: dict[str, Any], system_prompt: str):
    history = await asyncio.to_thread(_load_memory_for_session, state.get("session_id", ""))
    compaction_enabled = os.getenv("GAOBAO_CHAT_COMPACTION_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    options = _chat_context_options(compaction_enabled=compaction_enabled)
    current = build_llm_context(state)
    if compaction_enabled:
        # Explicit opt-in: this may call the configured LLM a second time.
        # Keep the original SQL history intact; do not persist model summaries
        # as authoritative admissions facts or long-term user preferences.
        from server.agent.memory_manager import DefaultMemoryManager

        manager = DefaultMemoryManager()
        return await build_compacted_chat_context(
            history,
            system_prompt,
            current,
            summarizer=manager._summarize_history,
            **options,
        )
    return build_chat_context(history, system_prompt, current, **options)


def _maybe_trim(messages: list[dict]) -> list[dict]:
    """Trim message list if it exceeds the context budget."""
    system_messages: list[dict] = []
    rest: list[dict] = []

    for m in messages:
        if m.get("role") == "system":
            system_messages.append(m)
        else:
            rest.append(m)

    max_to_keep = MAX_HISTORY_ROUNDS * 2
    if len(rest) > max_to_keep:
        dropped = len(rest) - max_to_keep
        rest = rest[-max_to_keep:]
        logger.info(
            "Context trimmed: dropped %d messages, keeping %d",
            dropped,
            len(rest),
        )

    return system_messages + rest


# ── Streaming (also sync generator, called inside SSE handler) ────


def llm_node_stream(state: dict[str, Any]) -> Generator[tuple[str, bool], None, None]:
    """Stream LLM tokens one by one with retry + context trimming + memory.

    Yields (token, is_degraded) tuples. is_degraded=True means LLM failed
    and a fallback reply is being streamed.
    """
    cfg = _get_config()

    try:
        client = _get_llm_client()
        system_prompt = _load_system_prompt()

        context = _build_chat_context_sync(state, system_prompt)
        messages = context.messages

        stream = _sync_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                temperature=cfg.get("temperature", 0.7),
                max_tokens=cfg.get("max_tokens") or 2000,
                stream=True,
            )
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield (chunk.choices[0].delta.content, False)
        return  # Normal completion
    except Exception:
        # Prefer the grounded template produced by render_reply when present;
        # it already carries the synthetic-data disclaimer.
        fallback = state.get("template_reply") or _FALLBACK_REPLY
        for char in fallback:
            yield (char, True)  # Degraded: stream fallback reply with degraded flag


# ── Final-answer candidate for the public SSE route ──────────────────


async def generate_chat_candidate(state: dict[str, Any]) -> tuple[str, bool]:
    """Build a complete, *unpublished* LLM draft.

    Buffering is intentional: unverified model tokens must not reach the
    client before source/quality validation and database persistence. Unlike
    the legacy synchronous generator this uses async network I/O and can be
    cancelled on client disconnect. Any partial output is discarded on error.
    """
    cfg = _get_config()
    key = str(cfg.get("api_key") or "").strip()
    fallback = state.get("template_reply") or _FALLBACK_REPLY
    if not key:
        return fallback, True

    try:
        context = await _build_chat_context_async(state, _load_system_prompt())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Chat context unavailable (type=%s)", type(exc).__name__)
        return fallback, True
    messages = context.messages
    logger.info(
        "Chat context prepared (status=%s, omitted=%d, estimate=%d)",
        context.status,
        context.omitted_messages,
        context.estimated_tokens,
    )

    async with AsyncOpenAI(
        api_key=key,
        base_url=cfg.get("base_url"),
        timeout=cfg.get("timeout", 120.0),
        max_retries=0,
    ) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                stream = await client.chat.completions.create(
                    model=cfg["model"],
                    messages=messages,
                    temperature=cfg.get("temperature", 0.7),
                    max_tokens=cfg.get("max_tokens") or 2000,
                    stream=True,
                )
                chunks: list[str] = []
                total_chars = 0
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        piece = chunk.choices[0].delta.content
                        total_chars += len(piece)
                        if total_chars > 16000:
                            raise ValueError("LLM draft exceeds final-answer budget")
                        chunks.append(piece)
                reply = "".join(chunks).strip()
                if not reply:
                    raise ValueError("LLM draft is empty")
                return reply, False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retry = status in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES
                logger.warning(
                    "Chat LLM candidate failed (type=%s, retry=%s)",
                    type(exc).__name__,
                    retry,
                )
                if not retry:
                    break
                await asyncio.sleep(BASE_DELAY * (2**attempt))
    return fallback, True
