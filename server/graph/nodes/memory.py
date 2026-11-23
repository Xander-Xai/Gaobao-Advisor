"""Memory node — persists conversation to database.

Upgraded from bare persistence to dual-layer memory management:
1. Recent N rounds preserved verbatim (in the DB conversation history).
2. Older rounds can be summarized via LLM for long-running conversations.

The actual summarization happens in MemoryManager; this node handles the
persistence side and flagging whether summarization is needed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
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
            if state.get("quality_scored") and reply:
                # Persist only the quality measurement for the *delivered*
                # reply, in the same transaction as conversation + slots.
                # Stable content digest; do not persist raw model critique or
                # discarded candidate text in the review record.
                from db.models import QualityScore

                scores = state.get("quality_scores") or {}
                flags = [
                    str(flag).split(":", 1)[0][:48]
                    for flag in (state.get("hallucination_flags") or [])[:20]
                ]
                db.add(
                    QualityScore(
                        conversation_id=session_id,
                        message_id=hashlib.sha256(reply.encode("utf-8")).hexdigest()[:32],
                        factual_score=scores.get("factual"),
                        relevance_score=scores.get("relevance"),
                        helpfulness_score=scores.get("helpfulness"),
                        style_score=scores.get("style"),
                        aggregate_score=float(state.get("aggregate_score") or 0),
                        hallucination_flags=json.dumps(flags),
                        judge_model=str(state.get("judge_model") or "unknown")[:64],
                        judge_latency_ms=state.get("judge_latency_ms"),
                    )
                )
            if state.get("review_required") and reply:
                # Privacy-safe, append-only operator-triage ticket. Raw user
                # text, profile, discarded LLM draft and session ID never enter
                # the audit ticket. Review acknowledgement is NOT factual
                # certification or approval to reuse a rejected answer.
                from db.models import OperationAuditLog

                ticket_id = secrets.token_hex(16)
                db.add(
                    OperationAuditLog(
                        actor="chat-finalizer",
                        role="service",
                        action="chat.review_required",
                        resource_type="chat_review",
                        resource_id=ticket_id,
                        outcome="PENDING",
                        detail_json=json.dumps(
                            {
                                "reason_codes": [
                                    str(code)[:48] for code in (state.get("review_reason_codes") or [])[:10]
                                ],
                                "delivered_reply_sha256": hashlib.sha256(reply.encode("utf-8")).hexdigest(),
                                "rewrite_attempts": min(int(state.get("rewrite_attempts") or 0), 1),
                                "data_profile": "SYNTHETIC_DEMO",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
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

    result = {"trace": trace}
    if trace and trace[-1].get("event") == "persisted" and state.get("atomic_memory") and state.get("review_required"):
        result["review_ticket_id"] = ticket_id
    return result
