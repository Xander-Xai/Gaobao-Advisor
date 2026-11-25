"""Completed-turn idempotency for authenticated industrial chat continuations."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass

from db.models import ChatTurnReceipt
from server.services.industrial_session import SessionConflict, _locked_row


class RequestKeyConflict(RuntimeError):
    """The key was already committed for a different request."""


@dataclass(frozen=True)
class TurnKey:
    key_digest: str
    request_digest: str


def make_turn_key(key: str, scene: str, message: str, slots: dict | None) -> TurnKey:
    encoded = json.dumps(
        {
            "scene": scene,
            "message": message,
            "slots": slots or {},
            "manifest_sha256": os.getenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return TurnKey(hashlib.sha256(key.encode()).hexdigest(), hashlib.sha256(encoded.encode()).hexdigest())


def lookup_receipt(session_id: str, key: TurnKey) -> dict | None:
    from db.database import get_session

    with get_session() as db:
        conv = _locked_row(db, session_id)
        if conv is None:
            raise SessionConflict("Session unavailable")
        row = db.get(ChatTurnReceipt, (conv.id, key.key_digest))
        if row is None:
            return None
        if row.request_digest != key.request_digest:
            raise RequestKeyConflict("Idempotency key reused for different request")
        response = row.response
        if not isinstance(response, dict) or not isinstance(response.get("reply"), str):
            raise RuntimeError("Invalid chat receipt")
        return response


def record_receipt(db, conversation_id: int, key: TurnKey, state: dict) -> dict:
    """Called under the conversation lock, in the exact message transaction."""
    response = {
        "reply": state["reply"],
        "structured_result": state["structured_result"],
        "slots": state.get("slots") or {},
        "message_id": secrets.token_hex(16),
    }
    db.add(
        ChatTurnReceipt(
            conversation_id=conversation_id,
            key_digest=key.key_digest,
            request_digest=key.request_digest,
            response=response,
        )
    )
    return response
