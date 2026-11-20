"""Admin-attested, synthetic-only optimization experiment review registry.

Uses the isolated local TaskRun SQLite journal. Account signoffs can attest
a frozen *self-declared* experiment report, but cannot verify provider usage
or approve a production deployment. "Stage" only moves an isolated demo
pointer; neither public /chat nor any Deep Agents worker reads it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

from benchmarks.synthetic_optimization_review import build_optimization_review
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict, TaskRunNotFound, _canonical

_ID_PATTERN = re.compile(r"^opt_[0-9a-f]{32}$")
_ALLOWED_ACTORS = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
_MAX_ROWS = 100


class SyntheticOptimizationReviewStore:
    """Small bounded ledger, independent from production runtime configuration."""

    def __init__(self, taskruns: SyntheticTaskRunStore) -> None:
        self.taskruns = taskruns
        with self.taskruns._open() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_optimizer_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    submitted_by TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL UNIQUE,
                    report_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_optimizer_signoffs (
                    proposal_id TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    reviewed_at REAL NOT NULL,
                    PRIMARY KEY(proposal_id, reviewer)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_optimizer_demo_pointer (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                    active_proposal_id TEXT,
                    pointer_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_optimizer_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO synthetic_optimizer_demo_pointer "
                "(singleton_id,active_proposal_id,pointer_version) VALUES (1,NULL,0)"
            )

    @staticmethod
    def _check_actor(actor: str) -> None:
        if not isinstance(actor, str) or not _ALLOWED_ACTORS.fullmatch(actor):
            raise ValueError("Invalid authenticated admin identity")

    @staticmethod
    def _check_id(proposal_id: str) -> None:
        if not isinstance(proposal_id, str) or not _ID_PATTERN.fullmatch(proposal_id):
            raise ValueError("Invalid synthetic optimization ID")

    @staticmethod
    def _event(conn: sqlite3.Connection, proposal_id: str, actor: str, kind: str, now: float) -> None:
        conn.execute(
            "INSERT INTO synthetic_optimizer_events (proposal_id,actor,kind,created_at) VALUES (?,?,?,?)",
            (proposal_id, actor, kind, now),
        )

    @staticmethod
    def _fetch(conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM synthetic_optimizer_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            raise TaskRunNotFound("Synthetic optimizer proposal not found")
        return row

    @staticmethod
    def _public(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        signoffs = conn.execute(
            "SELECT reviewer, verdict, reviewed_at FROM synthetic_optimizer_signoffs "
            "WHERE proposal_id=? ORDER BY reviewer",
            (row["proposal_id"],),
        ).fetchall()
        history = conn.execute(
            "SELECT actor,kind,created_at FROM synthetic_optimizer_events "
            "WHERE proposal_id=? ORDER BY event_id LIMIT 30",
            (row["proposal_id"],),
        ).fetchall()
        report = json.loads(row["report_json"])
        return {
            "proposal_id": row["proposal_id"],
            "data_profile": "SYNTHETIC_DEMO",
            "status": row["status"],
            "submitted_by": row["submitted_by"],
            "evidence_bundle_sha256": row["evidence_hash"],
            "report": report,
            "signoffs": [dict(item) for item in signoffs],
            "audit_events": [dict(item) for item in history],
            "source_report_independently_verified": False,
            "admin_signoffs_are_professional_expert_review": False,
            "staged_pointer_changes_runtime": False,
            "production_deployment_authorized": False,
            "real_admissions_accuracy": "NOT_MEASURED",
        }

    def submit(self, pairs: list[tuple[dict[str, Any], dict[str, Any]]], *, actor: str) -> dict[str, Any]:
        """Compute the gate server-side; a client may not supply approval fields."""
        self._check_actor(actor)
        report = build_optimization_review(pairs)
        digest = report["evidence_bundle_sha256"]
        proposal_id = "opt_" + digest[:32]
        now = time.time()
        initial = "PENDING_ADMIN_ATTESTATION" if report["evaluation_gate"] == "REVIEW_REQUIRED" else "BLOCKED_BY_EVAL"
        with self.taskruns._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                duplicate = conn.execute(
                    "SELECT * FROM synthetic_optimizer_proposals WHERE evidence_hash=?", (digest,)
                ).fetchone()
                if duplicate is not None:
                    # Submitting identical evidence is idempotent, never resets rejection/review.
                    answer = self._public(conn, duplicate)
                    conn.commit()
                    return answer
                count = conn.execute("SELECT COUNT(*) FROM synthetic_optimizer_proposals").fetchone()[0]
                if count >= _MAX_ROWS:
                    raise TaskRunConflict("Synthetic optimization proposal journal limit reached")
                conn.execute(
                    "INSERT INTO synthetic_optimizer_proposals "
                    "(proposal_id,submitted_by,evidence_hash,report_json,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (proposal_id, actor, digest, _canonical(report), initial, now, now),
                )
                self._event(conn, proposal_id, actor, "SUBMITTED_DECLARED_EVIDENCE", now)
                row = self._fetch(conn, proposal_id)
                answer = self._public(conn, row)
                conn.commit()
                return answer
            except Exception:
                conn.rollback()
                raise

    def get(self, proposal_id: str) -> dict[str, Any]:
        self._check_id(proposal_id)
        with self.taskruns._open() as conn:
            return self._public(conn, self._fetch(conn, proposal_id))

    def signoff(self, proposal_id: str, *, actor: str, verdict: str) -> dict[str, Any]:
        """Two separate authenticated admins; no credential/real-data inference."""
        self._check_id(proposal_id)
        self._check_actor(actor)
        if verdict not in {"attest_reviewed", "reject"}:
            raise ValueError("Unsupported review declaration")
        now = time.time()
        with self.taskruns._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = self._fetch(conn, proposal_id)
                if actor == row["submitted_by"]:
                    raise TaskRunConflict("Proposal submitter cannot attest its own report")
                previous = conn.execute(
                    "SELECT verdict FROM synthetic_optimizer_signoffs WHERE proposal_id=? AND reviewer=?",
                    (proposal_id, actor),
                ).fetchone()
                if previous is not None:
                    if previous["verdict"] != verdict:
                        raise TaskRunConflict("An attestation decision is immutable")
                    answer = self._public(conn, row)
                    conn.commit()
                    return answer
                if row["status"] != "PENDING_ADMIN_ATTESTATION":
                    raise TaskRunConflict("Proposal is not eligible for further attestation")
                reviews = conn.execute(
                    "SELECT COUNT(*) FROM synthetic_optimizer_signoffs WHERE proposal_id=?", (proposal_id,)
                ).fetchone()[0]
                if reviews >= 2:
                    raise TaskRunConflict("At most two independent admin signoffs")
                conn.execute(
                    "INSERT INTO synthetic_optimizer_signoffs "
                    "(proposal_id,reviewer,verdict,reviewed_at) VALUES (?,?,?,?)",
                    (proposal_id, actor, verdict, now),
                )
                if verdict == "reject":
                    new_status = "REJECTED_BY_ADMIN"
                elif reviews == 1:
                    new_status = "TWO_ADMIN_ATTESTATIONS_UNVERIFIED"
                else:
                    new_status = "PENDING_ADMIN_ATTESTATION"
                conn.execute(
                    "UPDATE synthetic_optimizer_proposals SET status=?,updated_at=? WHERE proposal_id=?",
                    (new_status, now, proposal_id),
                )
                self._event(conn, proposal_id, actor, "REVIEW_" + verdict.upper(), now)
                answer = self._public(conn, self._fetch(conn, proposal_id))
                conn.commit()
                return answer
            except Exception:
                conn.rollback()
                raise

    def simulate_stage(self, proposal_id: str, *, actor: str) -> dict[str, Any]:
        """Move an *unused demo pointer* only; no runtime rollout or true canary."""
        self._check_id(proposal_id)
        self._check_actor(actor)
        now = time.time()
        with self.taskruns._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = self._fetch(conn, proposal_id)
                reviews = conn.execute(
                    "SELECT reviewer,verdict FROM synthetic_optimizer_signoffs WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchall()
                if row["status"] != "TWO_ADMIN_ATTESTATIONS_UNVERIFIED":
                    raise TaskRunConflict("Two admin attestations required before sandbox pointer simulation")
                if (
                    len(reviews) != 2
                    or any(item["verdict"] != "attest_reviewed" for item in reviews)
                    or actor in {row["submitted_by"], *(item["reviewer"] for item in reviews)}
                ):
                    raise TaskRunConflict("Independent submit, review and simulated stage roles required")
                pointer = conn.execute("SELECT * FROM synthetic_optimizer_demo_pointer WHERE singleton_id=1").fetchone()
                if pointer["active_proposal_id"] is not None:
                    raise TaskRunConflict("Rollback current demo pointer before another simulation")
                conn.execute(
                    "UPDATE synthetic_optimizer_demo_pointer SET active_proposal_id=?,pointer_version=pointer_version+1 "
                    "WHERE singleton_id=1 AND active_proposal_id IS NULL",
                    (proposal_id,),
                )
                conn.execute(
                    "UPDATE synthetic_optimizer_proposals SET status='DEMO_POINTER_ACTIVE',updated_at=? "
                    "WHERE proposal_id=?",
                    (now, proposal_id),
                )
                self._event(conn, proposal_id, actor, "SIMULATED_STAGE_POINTER_ONLY", now)
                answer = self._public(conn, self._fetch(conn, proposal_id))
                conn.commit()
                return answer
            except Exception:
                conn.rollback()
                raise

    def rollback(self, *, actor: str) -> dict[str, Any]:
        """Revert the demo pointer, retaining reviewer and event records."""
        self._check_actor(actor)
        now = time.time()
        with self.taskruns._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                pointer = conn.execute("SELECT * FROM synthetic_optimizer_demo_pointer WHERE singleton_id=1").fetchone()
                proposal_id = pointer["active_proposal_id"]
                if proposal_id is None:
                    raise TaskRunConflict("No demo pointer is active")
                conn.execute(
                    "UPDATE synthetic_optimizer_demo_pointer SET active_proposal_id=NULL,"
                    "pointer_version=pointer_version+1 WHERE singleton_id=1",
                )
                conn.execute(
                    "UPDATE synthetic_optimizer_proposals SET status='DEMO_POINTER_ROLLED_BACK',updated_at=? "
                    "WHERE proposal_id=? AND status='DEMO_POINTER_ACTIVE'",
                    (now, proposal_id),
                )
                self._event(conn, proposal_id, actor, "SIMULATED_POINTER_ROLLBACK", now)
                result = self._demo_snapshot(conn)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _demo_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            "SELECT active_proposal_id,pointer_version FROM synthetic_optimizer_demo_pointer WHERE singleton_id=1"
        ).fetchone()
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "active_demo_pointer": row["active_proposal_id"],
            "pointer_version": row["pointer_version"],
            "effective_public_chat_prompt": "UNCHANGED",
            "effective_taskrun_deepagents_prompt": "UNCHANGED",
            "production_deployment_authorized": False,
            "real_canary_or_gray_rollout_executed": False,
        }

    def demo_snapshot(self) -> dict[str, Any]:
        with self.taskruns._open() as conn:
            return self._demo_snapshot(conn)
