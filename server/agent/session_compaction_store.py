"""Optional, versioned SQL summary cache for one authenticated conversation.

Never stores admissions facts or cross-user profile preferences. The snapshot
is tied to the *conversation primary key*, a SHA-256 digest of the exact covered
message prefix, a summary policy hash, a short TTL and CAS version.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Conversation, SessionCompactionSnapshot, _utcnow
from server.agent.chat_context import ChatContextError, _validate_history

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


@dataclass(frozen=True)
class CompactionSnapshot:
    summary: str
    covered_count: int
    version: int


def history_digest(history: list[dict[str, Any]]) -> str:
    safe = _validate_history(history)
    canonical = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def policy_digest(*, model_id: str, keep_recent_rounds: int, summary_trigger_tokens: int) -> str:
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
        raise ChatContextError("Invalid summary model identity")
    if type(keep_recent_rounds) is not int or keep_recent_rounds <= 0:
        raise ChatContextError("Invalid compaction rounds")
    if type(summary_trigger_tokens) is not int or summary_trigger_tokens <= 0:
        raise ChatContextError("Invalid compaction threshold")
    from server.agent.memory_manager import SUMMARY_PROMPT

    data = {
        "version": 1,
        "model": model_id,
        "rounds": keep_recent_rounds,
        "trigger": summary_trigger_tokens,
        "prompt": SUMMARY_PROMPT,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


class SessionCompactionStore:
    """Inject a Session factory to support SQLite and MySQL without global state."""

    def __init__(self, session_factory: Callable[[], Session], *, ttl_hours: int = 24):
        if type(ttl_hours) is not int or not 1 <= ttl_hours <= 168:
            raise ValueError("Snapshot TTL must be 1..168 hours")
        self.session_factory = session_factory
        self.ttl_hours = ttl_hours

    @staticmethod
    def _check_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not _SESSION_RE.fullmatch(session_id):
            raise ChatContextError("Invalid authenticated session identity")

    def load(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        *,
        policy_sha256: str,
    ) -> CompactionSnapshot | None:
        self._check_id(session_id)
        safe = _validate_history(history)
        with self.session_factory() as db:
            row = (
                db.query(SessionCompactionSnapshot)
                .join(Conversation, SessionCompactionSnapshot.conversation_id == Conversation.id)
                .filter(Conversation.session_id == session_id)
                .one_or_none()
            )
            if (
                row is None
                or row.expires_at <= _utcnow()
                or row.policy_sha256 != policy_sha256
                or row.covered_count > len(safe)
                or row.covered_count <= 0
                or len(row.summary) > 4000
                or not row.summary.strip()
            ):
                return None
            if history_digest(safe[: row.covered_count]) != row.covered_sha256:
                return None
            return CompactionSnapshot(row.summary, row.covered_count, row.version)

    def publish(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        *,
        covered_count: int,
        summary: str,
        policy_sha256: str,
        expected_version: int = 0,
    ) -> bool:
        """Publish only if DB prefix still matches and CAS expected version wins.

        Another concurrent writer can win; caller may still use its ephemeral
        summary for the current turn but must not overwrite the winner.
        """
        self._check_id(session_id)
        safe = _validate_history(history)
        if type(covered_count) is not int or not 1 <= covered_count <= len(safe):
            raise ChatContextError("Invalid covered message count")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 4000:
            raise ChatContextError("Invalid bounded summary")
        if not isinstance(policy_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", policy_sha256):
            raise ChatContextError("Invalid summary policy digest")
        if type(expected_version) is not int or expected_version < 0:
            raise ChatContextError("Invalid snapshot version")
        expected_digest = history_digest(safe[:covered_count])
        try:
            with self.session_factory() as db:
                conv = (
                    db.query(Conversation).filter(Conversation.session_id == session_id).with_for_update().one_or_none()
                )
                if conv is None:
                    return False
                # Verify against the *current* committed rows, not just the
                # request-local history. Rewrites/deletion must invalidate cache.
                actual_prefix = [{"role": m.role, "content": m.content} for m in conv.messages[:covered_count]]
                if len(actual_prefix) != covered_count or history_digest(actual_prefix) != expected_digest:
                    return False
                now = _utcnow()
                fields = {
                    "covered_count": covered_count,
                    "covered_sha256": expected_digest,
                    "policy_sha256": policy_sha256,
                    "summary": summary.strip(),
                    "expires_at": now + timedelta(hours=self.ttl_hours),
                    "updated_at": now,
                }
                if expected_version == 0:
                    prior = (
                        db.query(SessionCompactionSnapshot)
                        .filter(SessionCompactionSnapshot.conversation_id == conv.id)
                        .one_or_none()
                    )
                    if prior is not None:
                        prior_prefix = [
                            {"role": m.role, "content": m.content} for m in conv.messages[: prior.covered_count]
                        ]
                        still_valid = (
                            prior.expires_at > now
                            and prior.policy_sha256 == policy_sha256
                            and prior.covered_count > 0
                            and len(prior_prefix) == prior.covered_count
                            and history_digest(prior_prefix) == prior.covered_sha256
                            and bool(prior.summary.strip())
                        )
                        if still_valid:
                            return False
                        # Replace only a demonstrably stale/expired cache;
                        # concurrent replacement is guarded by the DB version.
                        replaced = (
                            db.query(SessionCompactionSnapshot)
                            .filter(
                                SessionCompactionSnapshot.conversation_id == conv.id,
                                SessionCompactionSnapshot.version == prior.version,
                            )
                            .update({**fields, "version": prior.version + 1}, synchronize_session=False)
                        )
                        if replaced != 1:
                            db.rollback()
                            return False
                    else:
                        db.add(SessionCompactionSnapshot(conversation_id=conv.id, version=1, **fields))
                else:
                    changed = (
                        db.query(SessionCompactionSnapshot)
                        .filter(
                            SessionCompactionSnapshot.conversation_id == conv.id,
                            SessionCompactionSnapshot.version == expected_version,
                            SessionCompactionSnapshot.expires_at > now,
                        )
                        .update({**fields, "version": expected_version + 1}, synchronize_session=False)
                    )
                    if changed != 1:
                        db.rollback()
                        return False
                db.commit()
                return True
        except IntegrityError:
            # Concurrent initial insert, or account/session deletion.
            return False

    def prune_expired(self, *, limit: int = 100) -> int:
        """Bounded physical expiry cleanup; operator must schedule separately."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid cleanup bound")
        with self.session_factory() as db:
            ids = (
                db.query(SessionCompactionSnapshot.conversation_id)
                .filter(SessionCompactionSnapshot.expires_at <= _utcnow())
                .order_by(SessionCompactionSnapshot.expires_at)
                .limit(limit)
                .all()
            )
            if not ids:
                return 0
            db.query(SessionCompactionSnapshot).filter(
                SessionCompactionSnapshot.conversation_id.in_([row[0] for row in ids])
            ).delete(synchronize_session=False)
            db.commit()
            return len(ids)
