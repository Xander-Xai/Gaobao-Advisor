"""Industrial chat optimistic concurrency on the authoritative conversation row."""

from __future__ import annotations

import json
from dataclasses import dataclass

from db.models import Conversation, ConversationMessage
from slots.persistence import extract_query_state, flatten_slots


class SessionConflict(RuntimeError):
    """The session disappeared or changed after the turn began."""


@dataclass(frozen=True)
class TurnVersion:
    conversation_id: int | None
    slots_json: str | None
    last_message_id: int | None


def _locked_row(db, session_id):
    return db.query(Conversation).filter(Conversation.session_id == session_id).with_for_update().first()


def _version(db, row) -> TurnVersion:
    if row is None:
        return TurnVersion(None, None, None)
    last = (
        db.query(ConversationMessage.id)
        .filter(ConversationMessage.conversation_id == row.id)
        .order_by(ConversationMessage.id.desc())
        .with_for_update()
        .first()
    )
    return TurnVersion(row.id, row.slots_json, last[0] if last else None)


def load_turn(session_id: str, provided: dict | None, *, fresh: bool) -> tuple[dict, dict, TurnVersion]:
    """Read a coherent version under the same lock used by commit and erasure."""
    from db.database import get_session

    with get_session() as db:
        row = _locked_row(db, session_id)
        if fresh != (row is None):
            raise SessionConflict("Session is missing or already exists")
        version = _version(db, row)
        stored = json.loads(row.slots_json) if row is not None and row.slots_json is not None else {}
        if not isinstance(stored, dict):
            raise ValueError("Invalid stored session slots")
    slots = flatten_slots(stored, derive_score=False)
    for key, value in (provided or {}).items():
        if (
            key in {"province", "score", "score_rank", "subject"}
            and key not in slots
            and value not in (None, "", False)
        ):
            slots[key] = value
    return slots, extract_query_state(stored), version


def claim_turn(db, session_id: str, expected: TurnVersion):
    """Lock and compare before writing; only a server-created fresh turn inserts."""
    row = _locked_row(db, session_id)
    if _version(db, row) != expected:
        raise SessionConflict("Session changed during turn")
    if row is None:
        row = Conversation(session_id=session_id)
        db.add(row)
        db.flush()
    return row
