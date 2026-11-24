"""Optional, versioned SQL summary cache for one authenticated conversation.

Never stores admissions facts or cross-user profile preferences. The snapshot
is tied to the *conversation primary key*, a SHA-256 digest of the exact covered
message prefix, a summary policy hash, a short TTL and CAS version.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Conversation, ConversationMessage, SessionCompactionSnapshot, _utcnow
from server.agent.chat_context import ChatContextError, _validate_history

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_ENCRYPTED_PREFIX = "enc:v1:"


def snapshot_encryption_key() -> bytes | None:
    """Read a local secret without logging it; reject invalid key material."""
    raw = os.getenv("GAOBAO_CHAT_SNAPSHOT_KEY", "").strip()
    required = os.getenv("GAOBAO_CHAT_SNAPSHOT_REQUIRE_ENCRYPTION", "false").lower() == "true"
    if not raw:
        if required:
            raise RuntimeError("Snapshot encryption key is required")
        return None
    if not re.fullmatch(r"[a-fA-F0-9]{64}", raw):
        raise RuntimeError("Snapshot encryption key must contain 64 hex characters")
    return bytes.fromhex(raw)


def validate_snapshot_startup() -> None:
    enabled = os.getenv("GAOBAO_CHAT_SNAPSHOT_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    if os.getenv("APP_ENV", "").strip().lower() not in {"development", "test", "testing", "local"}:
        raise RuntimeError("Snapshot production privacy approval is required")
    snapshot_encryption_key()


@dataclass(frozen=True)
class CompactionSnapshot:
    summary: str
    covered_count: int
    version: int


def history_digest(history: list[dict[str, Any]]) -> str:
    safe = _validate_history(history)
    canonical = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def policy_digest(
    *,
    model_id: str,
    keep_recent_rounds: int,
    summary_trigger_tokens: int,
    provider_identity: str = "",
) -> str:
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
        raise ChatContextError("Invalid summary model identity")
    if not isinstance(provider_identity, str) or len(provider_identity) > 2048:
        raise ChatContextError("Invalid summary provider identity")
    if type(keep_recent_rounds) is not int or keep_recent_rounds <= 0:
        raise ChatContextError("Invalid compaction rounds")
    if type(summary_trigger_tokens) is not int or summary_trigger_tokens <= 0:
        raise ChatContextError("Invalid compaction threshold")
    from server.agent.memory_manager import SUMMARY_PROMPT

    data = {
        "version": 1,
        "model": model_id,
        "provider": provider_identity,
        "rounds": keep_recent_rounds,
        "trigger": summary_trigger_tokens,
        "prompt": SUMMARY_PROMPT,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


class SessionCompactionStore:
    """Inject a Session factory to support SQLite and MySQL without global state."""

    def __init__(
        self, session_factory: Callable[[], Session], *, ttl_hours: int = 24, encryption_key: bytes | None = None
    ):
        if type(ttl_hours) is not int or not 1 <= ttl_hours <= 168:
            raise ValueError("Snapshot TTL must be 1..168 hours")
        self.session_factory = session_factory
        self.ttl_hours = ttl_hours
        self.cipher = AESGCM(encryption_key) if encryption_key is not None else None

    @staticmethod
    def _aad(conversation_id: int, covered_count: int, covered_sha256: str, policy_sha256: str) -> bytes:
        return json.dumps(
            ["gaobao-session-summary/v1", conversation_id, covered_count, covered_sha256, policy_sha256],
            separators=(",", ":"),
        ).encode()

    def _encode_summary(self, summary: str, aad: bytes) -> str:
        if self.cipher is None:
            return summary
        nonce = os.urandom(12)
        encrypted = self.cipher.encrypt(nonce, summary.encode("utf-8"), aad)
        return _ENCRYPTED_PREFIX + base64.b64encode(nonce + encrypted).decode("ascii")

    def _decode_summary(self, row: SessionCompactionSnapshot) -> str | None:
        value = row.summary
        if not isinstance(value, str):
            return None
        if self.cipher is None:
            return value if not value.startswith(_ENCRYPTED_PREFIX) and 1 <= len(value.strip()) <= 4000 else None
        if not value.startswith(_ENCRYPTED_PREFIX) or len(value) > 25000:
            return None
        try:
            raw = base64.b64decode(value[len(_ENCRYPTED_PREFIX) :], validate=True)
            aad = self._aad(row.conversation_id, row.covered_count, row.covered_sha256, row.policy_sha256)
            summary = self.cipher.decrypt(raw[:12], raw[12:], aad).decode("utf-8")
        except (InvalidTag, ValueError):
            return None
        return summary if 1 <= len(summary.strip()) <= 4000 else None

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
            ):
                return None
            if history_digest(safe[: row.covered_count]) != row.covered_sha256:
                return None
            # The caller's history may have become stale after a concurrent
            # edit, despite matching the snapshot. Re-check the committed
            # conversation prefix during *every* cache read.
            db_prefix = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == row.conversation_id)
                .order_by(ConversationMessage.id)
                .limit(row.covered_count)
                .all()
            )
            current = [{"role": msg.role, "content": msg.content} for msg in db_prefix]
            if len(current) != row.covered_count or history_digest(current) != row.covered_sha256:
                return None
            summary = self._decode_summary(row)
            return CompactionSnapshot(summary, row.covered_count, row.version) if summary is not None else None

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
                    "summary": self._encode_summary(
                        summary.strip(), self._aad(conv.id, covered_count, expected_digest, policy_sha256)
                    ),
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
                            and self._decode_summary(prior) is not None
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
            # A concurrent writer may refresh a row after selecting IDs.
            # Recheck expiry at DELETE time instead of erasing refreshed data.
            deleted = (
                db.query(SessionCompactionSnapshot)
                .filter(
                    SessionCompactionSnapshot.conversation_id.in_([row[0] for row in ids]),
                    SessionCompactionSnapshot.expires_at <= _utcnow(),
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            return deleted
