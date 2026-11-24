"""Memory node — persists conversation to database.

Upgraded from bare persistence to dual-layer memory management:
1. Recent N rounds preserved verbatim (in the DB conversation history).
2. Older rounds can be summarized via LLM for long-running conversations.

The actual summarization happens in MemoryManager; this node handles the
persistence side and flagging whether summarization is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from db.crud import get_or_create_conversation, load_conversation_slots, save_message, save_slots
from db.database import get_session
from server.agent.llm_reliability import estimate_messages_tokens
from slots.persistence import normalize_slots

logger = logging.getLogger(__name__)

# How many rounds before we flag for summarization
SUMMARIZE_AFTER_ROUNDS = 15


def memory_node(state: dict[str, Any]) -> dict[str, Any]:
    """Persist conversation data and flag if summarization is needed.

    This node runs at the end of every graph invocation:
    - Saves user input and assistant reply to the database.
    - Updates slot values.
    - Checks whether the conversation has grown long enough to warrant
      summarization (reported via a flag in the trace).

    The actual summarization (LLM call) is done by MemoryManager
    via build_memory_messages() at the start of the next graph invocation.
    """
    session_id = state.get("session_id", "")
    slots = dict(state.get("slots", {}) or {})
    # `_query_state` is a top-level graph key, but it is persisted inside
    # `slots_json` (see routes/profile.py::_load_query_state). Without this merge
    # it is dropped on every turn, so the questioning round counter and the
    # already-asked field list reset each time the user answers.
    query_state = state.get("_query_state")
    if query_state:
        slots["_query_state"] = query_state

    input_text = state.get("input_text", "")
    reply = state.get("reply", "")

    trace = list(state.get("trace", []))

    if not session_id:
        trace.append({"node": "memory_update", "event": "skipped_no_session_id"})
        return {"trace": trace}

    db = get_session()
    try:
        conv = get_or_create_conversation(db, session_id)
        atomic = bool(state.get("atomic_memory"))
        if input_text:
            if atomic:
                save_message(db, session_id, "user", input_text, commit=False)
            else:
                save_message(db, session_id, "user", input_text)
        if reply:
            if atomic:
                save_message(db, session_id, "assistant", reply, commit=False)
            else:
                save_message(db, session_id, "assistant", reply)
        # Store canonical slot records and merge whatever the graph did not
        # touch, so a partially-filled state cannot erase an existing profile.
        existing = load_conversation_slots(db, session_id) or {}
        canonical = normalize_slots(slots, existing=existing)
        if query_state:
            canonical["_query_state"] = query_state
        if atomic:
            save_slots(db, session_id, canonical, commit=False)
            db.commit()
        else:
            save_slots(db, session_id, canonical)

        # Check conversation length for summarization need
        msg_count = len(conv.messages) if conv.id else 0
        needs_summary = msg_count > SUMMARIZE_AFTER_ROUNDS * 2

        estimated_tokens = estimate_messages_tokens([{"content": m.content} for m in (conv.messages or [])])

        trace.append(
            {
                "node": "memory_update",
                "event": "persisted",
                "msg_count": msg_count,
                "estimated_tokens": estimated_tokens,
                "needs_summary": needs_summary,
            }
        )
    except Exception as exc:
        db.rollback()
        logger.warning("memory_update failed (error type: %s)", type(exc).__name__)
        trace.append(
            {
                "node": "memory_update",
                "event": "persist_failed",
                "error": "persistence_error",
            }
        )
    finally:
        db.close()

    return {"trace": trace}
