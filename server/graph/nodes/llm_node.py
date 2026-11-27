"""LLM reasoning node — calls the LLM to generate a conversational reply.

This module provides both synchronous (graph node) and streaming (SSE) entry
points with context trimming, token estimation, and conversation memory
management (dual-layer: recent + summary).

Every model call goes through :mod:`server.services.llm_gateway`, so the
provider RPM, TPM and concurrency budget of the LLM Resource Governor applies
to the internal calls of one HTTP request as well. The gateway also owns the
retry loop: a retry is a new governor admission, never a free second call.

Migration notes:
- retry_api_call     ← backend/agent/core.py:31-51 (now inside the gateway)
- trim_messages      ← backend/agent/core.py:54-69
- estimate_tokens    ← backend/agent/langchain_agent.py:37-39
- MemoryManager      ← backend/agent/langchain_agent.py:106-149
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Generator
from typing import Any

from config.loader import load_llm_config
from server.agent.chat_context import build_chat_context, build_compacted_chat_context
from server.agent.llm_reliability import (
    build_llm_context,
    estimate_messages_tokens,
)
from server.services.llm_gateway import governed_async_client, governed_client
from server.user_memory import prompt_context

MODEL = "gpt-4o"  # Default model if config fails
logger = logging.getLogger(__name__)


# ── Context constants ──────────────────────────────────────────────────────
MAX_HISTORY_ROUNDS = 20

# ── Client (lazy singleton) ────────────────────────────────────────────────
_client: Any | None = None
_async_client: Any | None = None
_client_lock = threading.Lock()
_config: dict | None = None
_FALLBACK_REPLY = "抱歉，我现在暂时无法给出完整分析。请稍后再试，或者告诉我你的省份和分数，我帮你做个初步判断。"


def _get_config() -> dict:
    global _config
    if _config is None:
        _config = load_llm_config()
    return _config


def _get_llm_client():
    """Return the governed synchronous client for this process."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = governed_client(operation="chat")
    return _client


def _get_async_llm_client():
    """Return the governed asynchronous client for this process."""
    global _async_client
    if _async_client is None:
        with _client_lock:
            if _async_client is None:
                _async_client = governed_async_client(operation="chat")
    return _async_client


def _load_system_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "system_prompt.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "你是一个资深高考志愿规划师。"


# ── Graph node (sync, runs inside asyncio.to_thread) ──────────────


def llm_node(state: dict[str, Any]) -> dict[str, Any]:
    """Call the LLM with context trimming + memory (non-streaming).

    Loads conversation history from the database via MemoryManager,
    applies dual-layer memory (recent verbatim + older summarized),
    trims if needed, then calls the LLM through the governed client.

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

        response = client.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            temperature=cfg.get("temperature", 0.7),
            max_tokens=cfg.get("max_tokens") or 2000,
        )
        content = response.choices[0].message.content
        reply = (content or "").strip()
        if not reply:
            reply = _FALLBACK_REPLY
    except Exception as exc:
        logger.warning("LLM call failed after retries: %s", type(exc).__name__)
        reply = _FALLBACK_REPLY
        trace.append(
            {
                "node": "llm_reason",
                "event": "llm_error",
                "error": type(exc).__name__,
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


def _current_prompt_with_preferences(state: dict[str, Any]) -> str:
    current = build_llm_context(state)
    preferences = prompt_context(state.get("user_memory", []))
    return preferences + "\n\n当前任务：\n" + current if preferences else current


def _system_prompt_with_skill(state: dict[str, Any], system_prompt: str) -> str:
    """Keep server-owned safety and selected methodology outside untrusted RAG/history."""
    skill = state.get("skill_context")
    if not isinstance(skill, str) or not skill.strip():
        from server.graph.nodes.quality_nodes import _get_skill_service

        skill = _get_skill_service().build_disclosed_context("general", max_chars=5500)
    return (
        system_prompt
        + "\n\n[服务端专业方法与安全约束；不是招生事实或引文。安全规则优先，方法论须服从已核验数据。]\n"
        + skill
    )


def _build_chat_context_sync(state: dict[str, Any], system_prompt: str):
    history = _load_memory_for_session(state.get("session_id", ""))
    return build_chat_context(
        history,
        _system_prompt_with_skill(state, system_prompt),
        _current_prompt_with_preferences(state),
        **_chat_context_options(),
    )


async def _build_chat_context_async(state: dict[str, Any], system_prompt: str):
    system_prompt = _system_prompt_with_skill(state, system_prompt)
    history = await asyncio.to_thread(_load_memory_for_session, state.get("session_id", ""))
    compaction_enabled = os.getenv("GAOBAO_CHAT_COMPACTION_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    options = _chat_context_options(compaction_enabled=compaction_enabled)
    current = _current_prompt_with_preferences(state)
    if compaction_enabled:
        # Explicit opt-in: this may call the configured LLM a second time.
        # Keep the original SQL history intact; do not persist model summaries
        # as authoritative admissions facts or long-term user preferences.
        from server.agent.memory_manager import DefaultMemoryManager

        manager = DefaultMemoryManager()
        use_snapshot = os.getenv("GAOBAO_CHAT_SNAPSHOT_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if use_snapshot:
            if os.getenv("APP_ENV", "").strip().lower() not in {"development", "test", "testing", "local"}:
                raise RuntimeError("Explicit non-production APP_ENV is required for plaintext snapshots")
            from db.database import get_session
            from server.agent.durable_chat_context import build_durable_chat_context
            from server.agent.session_compaction_store import (
                SessionCompactionStore,
                policy_digest,
                snapshot_encryption_key,
            )

            policy = policy_digest(
                model_id=str(_get_config().get("model") or ""),
                provider_identity=str(_get_config().get("base_url") or ""),
                keep_recent_rounds=options["keep_recent_rounds"],
                summary_trigger_tokens=manager.max_token_limit,
            )
            return await build_durable_chat_context(
                session_id=state.get("session_id", ""),
                history=history,
                system_prompt=system_prompt,
                current_prompt=current,
                store=SessionCompactionStore(get_session, encryption_key=snapshot_encryption_key()),
                policy_sha256=policy,
                summarizer=manager._summarize_history,
                summary_trigger_tokens=manager.max_token_limit,
                **options,
            )
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
    """Stream LLM tokens one by one with context trimming + memory.

    Yields (token, is_degraded) tuples. is_degraded=True means LLM failed
    and a fallback reply is being streamed. The governed client releases the
    provider concurrency slot when the stream ends, fails or is abandoned.
    """
    cfg = _get_config()

    try:
        client = _get_llm_client()
        system_prompt = _load_system_prompt()

        context = _build_chat_context_sync(state, system_prompt)
        messages = context.messages

        stream = client.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            temperature=cfg.get("temperature", 0.7),
            max_tokens=cfg.get("max_tokens") or 2000,
            stream=True,
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

    The call goes through the governed client, so a cancelled stream still
    returns its provider concurrency slot and its token reservation is
    reconciled with the tokens that were actually produced.
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

    client = _get_async_llm_client()
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
        logger.warning("Chat LLM candidate failed (type=%s)", type(exc).__name__)
        return fallback, True
