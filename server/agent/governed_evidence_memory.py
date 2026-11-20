"""Explicitly published, source-bound synthetic portfolio memory references.

This is NOT user-profile memory and is never injected into public /chat.
Only already-frozen, two-admin-reviewed SYNTHETIC_DEMO receipts can be indexed.
The index stores *no copied facts*: every read independently rechecks source SQL,
review status, provenance digest, TTL, and owner authorization. Deletion removes
the indexed memory pointer, NOT its original TaskRun or review/audit records.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from server.agent.synthetic_portfolio import PortfolioRequest, verify_synthetic_portfolio
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict, TaskRunNotFound, _canonical

_MAX_TTL_SECONDS = 30 * 86400
_MAX_ACTIVE_MEMORIES_PER_OWNER = 50
_KIND = "REVIEWED_SYNTHETIC_PORTFOLIO_POINTER"


class SyntheticEvidenceMemoryStore:
    """Explicit owner-scoped memory index on the isolated TaskRun journal."""

    def __init__(self, taskruns: SyntheticTaskRunStore) -> None:
        self.taskruns = taskruns
        with self.taskruns._open() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_evidence_memory (
                    owner TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    source_run_id TEXT NOT NULL UNIQUE,
                    receipt_sha256 TEXT NOT NULL,
                    published_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(owner, memory_key)
                )
                """
            )

    @staticmethod
    def _source(conn: sqlite3.Connection, run_id: str) -> tuple[sqlite3.Row, str, str, dict[str, Any]]:
        """Fail closed if source/reviews/SQL are stale or incomplete."""
        row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["engine"] != "portfolio" or row["status"] != "SUCCEEDED":
            raise TaskRunConflict("Only successful synthetic portfolio receipts can enter memory")
        review = SyntheticTaskRunStore._review_snapshot(conn, row)
        if review["review_state"] != "TWO_ADMIN_SIGNOFFS_SYNTHETIC_ONLY":
            raise TaskRunConflict("Two separate positive synthetic review signoffs are required")
        payload = json.loads(row["payload_json"])
        SyntheticTaskRunStore._validate_payload(payload, "portfolio")
        profile = payload["portfolio"]
        request = PortfolioRequest(
            province=profile["province"],
            score=profile["score"],
            subject_type=profile["subject_type"],
            year=profile["year"],
            candidates=tuple((item["school"], item["major"]) for item in profile["candidates"]),
        )
        result = json.loads(row["result_json"])
        # Requery current database; old receipts may not become future facts
        # merely because someone reviewed their historical digest.
        if not verify_synthetic_portfolio(request, result):
            raise TaskRunConflict("Synthetic SQL evidence no longer matches its current source")
        memory_key = hashlib.sha256(_canonical(profile).encode("utf-8")).hexdigest()
        return row, memory_key, review["receipt_sha256"], result

    @staticmethod
    def _public(row: sqlite3.Row, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_kind": _KIND,
            "data_profile": "SYNTHETIC_DEMO",
            "source_run_id": row["source_run_id"],
            "source_receipt_sha256": row["receipt_sha256"],
            "published_by": row["published_by"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "historical_comparisons": result["accepted_historical_comparisons"],
            "excluded_candidates": result["excluded_candidates"],
            "verified_against_current_synthetic_sql": True,
            "two_admin_synthetic_review_declared": True,
            "independently_verified_expert_review": False,
            "real_admissions_accuracy": "NOT_MEASURED",
            "automatic_model_memory_injection": False,
            "production_release_authorized": False,
        }

    def publish(self, run_id: str, *, publisher: str, ttl_seconds: int = 86400) -> dict[str, Any]:
        """Admin-authenticated operator action (auth handled by the HTTP endpoint).

        Two previously authenticated review signoffs must belong to OTHER
        users than the task creator and execution approver.
        """
        self.taskruns._validate_run_id(run_id)
        if not isinstance(publisher, str) or not 3 <= len(publisher) <= 64:
            raise ValueError("An authenticated administrative publisher is required")
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("Memory TTL must be 60 seconds to 30 days")
        now = time.time()
        with self.taskruns._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                source, memory_key, digest, result = self._source(conn, run_id)
                owner = source["owner"]
                existing = conn.execute(
                    "SELECT * FROM synthetic_evidence_memory WHERE owner=? AND memory_key=?",
                    (owner, memory_key),
                ).fetchone()
                if existing is not None and existing["expires_at"] > now:
                    if existing["source_run_id"] != run_id or existing["receipt_sha256"] != digest:
                        raise TaskRunConflict("Conflicting source for the same frozen synthetic memory key")
                    # An idempotent publish must NOT silently extend the TTL.
                    conn.commit()
                    return self._public(existing, result)
                if existing is not None:
                    conn.execute(
                        "DELETE FROM synthetic_evidence_memory WHERE owner=? AND memory_key=?",
                        (owner, memory_key),
                    )
                count = conn.execute(
                    "SELECT COUNT(*) FROM synthetic_evidence_memory WHERE owner=? AND expires_at>?",
                    (owner, now),
                ).fetchone()[0]
                if count >= _MAX_ACTIVE_MEMORIES_PER_OWNER:
                    raise TaskRunConflict("Owner reached the synthetic memory reference limit")
                conn.execute(
                    "INSERT INTO synthetic_evidence_memory "
                    "(owner,memory_key,source_run_id,receipt_sha256,published_by,created_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (owner, memory_key, run_id, digest, publisher, now, now + ttl_seconds),
                )
                saved = conn.execute(
                    "SELECT * FROM synthetic_evidence_memory WHERE source_run_id=?", (run_id,)
                ).fetchone()
                conn.commit()
                return self._public(saved, result)
            except Exception:
                conn.rollback()
                raise

    def get(self, run_id: str, *, requester: str, admin: bool = False) -> dict[str, Any]:
        """Never expose another owner's source; recheck current SQL on every read."""
        self.taskruns.get(run_id, requester=requester, admin=admin)
        with self.taskruns._open() as conn:
            saved = conn.execute("SELECT * FROM synthetic_evidence_memory WHERE source_run_id=?", (run_id,)).fetchone()
            if saved is None or saved["expires_at"] <= time.time():
                raise TaskRunNotFound("No active synthetic memory for this task")
            source, memory_key, digest, result = self._source(conn, run_id)
            if (
                saved["owner"] != source["owner"]
                or saved["memory_key"] != memory_key
                or saved["receipt_sha256"] != digest
            ):
                raise TaskRunConflict("Memory provenance has changed; explicit republish required")
            return self._public(saved, result)

    def list_for_owner(self, owner: str) -> dict[str, Any]:
        """Bounded owner-only retrieval with fail-closed source verification."""
        if not isinstance(owner, str) or not 3 <= len(owner) <= 64:
            raise ValueError("Invalid memory owner")
        now = time.time()
        with self.taskruns._open() as conn:
            rows = conn.execute(
                "SELECT source_run_id FROM synthetic_evidence_memory "
                "WHERE owner=? AND expires_at>? ORDER BY created_at DESC LIMIT ?",
                (owner, now, _MAX_ACTIVE_MEMORIES_PER_OWNER),
            ).fetchall()
        active: list[dict[str, Any]] = []
        rejected_count = 0
        for item in rows:
            try:
                active.append(self.get(item["source_run_id"], requester=owner))
            except (TaskRunConflict, TaskRunNotFound, ValueError, KeyError, TypeError):
                rejected_count += 1
        return {
            "memory_kind": _KIND,
            "data_profile": "SYNTHETIC_DEMO",
            "owner_scoped": True,
            "active_count": len(active),
            "blocked_stale_count": rejected_count,
            "items": active,
            "automatic_model_memory_injection": False,
            "real_admissions_accuracy": "NOT_MEASURED",
        }

    def forget(self, run_id: str, *, requester: str, admin: bool = False) -> dict[str, Any]:
        """Delete ONLY the indexed memory reference, never its original receipt."""
        source = self.taskruns.get(run_id, requester=requester, admin=admin)
        with self.taskruns._open() as conn:
            changed = conn.execute(
                "DELETE FROM synthetic_evidence_memory WHERE source_run_id=? AND owner=?",
                (run_id, source["owner"]),
            )
            if changed.rowcount != 1:
                raise TaskRunNotFound("No indexed synthetic memory for this task")
        return {
            "deleted": True,
            "memory_kind": _KIND,
            "source_taskrun_retained": True,
            "real_admissions_accuracy": "NOT_MEASURED",
        }
