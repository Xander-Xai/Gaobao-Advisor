"""Durable *synthetic-only* TaskRun journal and atomic maker-checker transitions.

SQLite here is a local single-service demonstration store. The worker claims
leases with BEGIN IMMEDIATE and a per-attempt token; an expired worker cannot
publish a stale completion. Expired read-only tasks may be replayed, so this
does NOT offer exactly-once side-effect execution or distributed resilience.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_RUN_RE = re.compile(r"^syn_[0-9a-f]{32}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,96}$")
_ALLOWED_ENGINES = frozenset({"deterministic", "deepagents", "portfolio"})
_MAX_ATTEMPTS = 2
_MAX_OUTSTANDING_PER_OWNER = 8
_MAX_TASK_JOURNAL_ROWS = 5000


class TaskRunConflict(RuntimeError):
    """Illegal transition, stale lease or idempotency-key collision."""


class TaskRunNotFound(LookupError):
    """A run is missing or not visible to the requester."""


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SyntheticTaskRunStore:
    """Versioned operator task queue, independent of the admissions DB."""

    def __init__(self, path: Path) -> None:
        original = Path(path)
        if not original.is_absolute():
            raise ValueError("Task journal path must be absolute")
        self.path = original.resolve()
        if not self.path.parent.is_dir():
            raise ValueError("Task journal requires an existing directory")
        # Never open the admissions SQLite as an Agent job journal.
        import os

        existing_database = os.getenv("DATABASE_URL", "")
        if existing_database.startswith("sqlite:///"):
            admissions_path = Path(existing_database.removeprefix("sqlite:///")).resolve()
            if admissions_path == self.path:
                raise ValueError("Task journal must be separate from admissions database")
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Task journal must be a regular file")
        with self._open() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_task_runs (
                    run_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approved_by TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_deadline REAL,
                    result_json TEXT,
                    failure_code TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(owner, idempotency_key)
                )
                """
            )

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not isinstance(run_id, str) or not _RUN_RE.fullmatch(run_id):
            raise ValueError("Invalid synthetic task ID")

    @staticmethod
    def _validate_payload(payload: dict[str, Any], engine: str) -> None:
        if engine not in _ALLOWED_ENGINES:
            raise ValueError("Unsupported execution engine")
        if not isinstance(payload, dict) or payload.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("TaskRun accepts synthetic data only")
        if engine == "portfolio":
            from server.agent.synthetic_portfolio import PortfolioRequest

            if set(payload) != {"data_profile", "portfolio"} or not isinstance(payload["portfolio"], dict):
                raise ValueError("Only the frozen synthetic portfolio contract is accepted")
            options = payload["portfolio"]
            if set(options) != {"province", "score", "subject_type", "year", "candidates"}:
                raise ValueError("Unexpected portfolio fields")
            candidates = options["candidates"]
            if not isinstance(candidates, list) or any(
                not isinstance(item, dict)
                or set(item) != {"school", "major"}
                or type(item["school"]) is not str
                or type(item["major"]) is not str
                for item in candidates
            ):
                raise ValueError("Unsupported portfolio candidate fields")
            PortfolioRequest(
                options["province"],
                options["score"],
                options["subject_type"],
                options["year"],
                tuple((item["school"], item["major"]) for item in candidates),
            )
            if len(_canonical(payload)) > 3000:
                raise ValueError("Synthetic portfolio exceeds task budget")
            return
        if set(payload) != {"data_profile", "profile"} or not isinstance(payload["profile"], dict):
            raise ValueError("Only the frozen synthetic decision-card contract is accepted")
        # Reject free-form prompts, secrets and any fields not in the Pydantic contract.
        from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
        from server.agent.synthetic_decision_card import DecisionCardProfile

        fields = {
            "school_a",
            "school_b",
            "major",
            "province",
            "score",
            "subject_type",
            "year",
        }
        profile = payload["profile"]
        if set(profile) != fields:
            raise ValueError("Unexpected profile fields")
        if (
            profile["school_a"] not in {s[0] for s in DEMO_SCHOOLS}
            or profile["school_b"] not in {s[0] for s in DEMO_SCHOOLS}
            or profile["school_a"] == profile["school_b"]
            or profile["major"] not in {m[0] for m in DEMO_MAJORS}
            or profile["province"] not in {"示例省·甲", "示例省·乙", "示例省·丙", "示例省·丁"}
            or type(profile["score"]) is not int
            or not 100 <= profile["score"] <= 750
            or profile["subject_type"] not in {"物理类", "历史类"}
            or type(profile["year"]) is not int
            or profile["year"] != DEMO_YEAR
        ):
            raise ValueError("Task payload must reference frozen fictional entities")
        DecisionCardProfile(**profile)
        if len(_canonical(payload)) > 2400:
            raise ValueError("Task profile too large")

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "owner": row["owner"],
            "engine": row["engine"],
            "status": row["status"],
            "approved_by": row["approved_by"],
            "version": row["version"],
            "attempts": row["attempts"],
            "failure_code": row["failure_code"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "data_profile": "SYNTHETIC_DEMO",
        }

    def create(self, payload: dict[str, Any], engine: str, owner: str, idempotency_key: str) -> dict[str, Any]:
        self._validate_payload(payload, engine)
        if not isinstance(owner, str) or not 3 <= len(owner) <= 64:
            raise ValueError("Invalid task owner")
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key")
        digest = hashlib.sha256((engine + _canonical(payload)).encode()).hexdigest()
        run_id = "syn_" + secrets.token_hex(16)
        now = time.time()
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM synthetic_task_runs WHERE owner=? AND idempotency_key=?",
                    (owner, idempotency_key),
                ).fetchone()
                if row is not None:
                    if row["payload_hash"] != digest:
                        raise TaskRunConflict("Idempotency key was used with different task details")
                    conn.commit()
                    return self._public(row)
                outstanding = conn.execute(
                    "SELECT COUNT(*) FROM synthetic_task_runs "
                    "WHERE owner=? AND status IN ('PENDING_APPROVAL','APPROVED','RUNNING')",
                    (owner,),
                ).fetchone()[0]
                if outstanding >= _MAX_OUTSTANDING_PER_OWNER:
                    raise TaskRunConflict("Owner outstanding TaskRun quota reached")
                total = conn.execute("SELECT COUNT(*) FROM synthetic_task_runs").fetchone()[0]
                if total >= _MAX_TASK_JOURNAL_ROWS:
                    raise TaskRunConflict("Synthetic TaskRun journal storage quota reached")
                conn.execute(
                    "INSERT INTO synthetic_task_runs "
                    "(run_id,owner,idempotency_key,payload_hash,payload_json,engine,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,? ,?,'PENDING_APPROVAL',?,?)",
                    (run_id, owner, idempotency_key, digest, _canonical(payload), engine, now, now),
                )
                row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                conn.commit()
                return self._public(row)
            except Exception:
                conn.rollback()
                raise

    def get(self, run_id: str, requester: str | None = None, admin: bool = False) -> dict[str, Any]:
        self._validate_run_id(run_id)
        with self._open() as conn:
            row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or (requester is not None and requester != row["owner"] and not admin):
            raise TaskRunNotFound("No visible synthetic TaskRun")
        return self._public(row)

    def approve(self, run_id: str, principal: str) -> dict[str, Any]:
        """Call only after the FastAPI dependency verified role == admin."""
        self._validate_run_id(run_id)
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise TaskRunNotFound("TaskRun not found")
                if row["owner"] == principal:
                    raise TaskRunConflict("Maker cannot approve their own task")
                if row["status"] != "PENDING_APPROVAL":
                    raise TaskRunConflict("TaskRun is not awaiting approval")
                conn.execute(
                    "UPDATE synthetic_task_runs SET status='APPROVED',approved_by=?,version=version+1,"
                    "updated_at=? WHERE run_id=?",
                    (principal, time.time(), run_id),
                )
                updated = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                conn.commit()
                return self._public(updated)
            except Exception:
                conn.rollback()
                raise

    def cancel(self, run_id: str, principal: str, *, admin: bool = False) -> dict[str, Any]:
        """Cancel a synthetic task atomically and fence every prior worker.

        For a RUNNING task this revokes the journal lease and prevents any
        result publication. It does not interrupt a blocking model or thread.
        """
        self._validate_run_id(run_id)
        if not isinstance(principal, str) or not 3 <= len(principal) <= 64:
            raise ValueError("Invalid cancellation principal")
        now = time.time()
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None or (row["owner"] != principal and not admin):
                    raise TaskRunNotFound("No visible synthetic TaskRun")
                if row["status"] == "CANCELLED":
                    conn.commit()
                    return self._public(row)
                if row["status"] not in {"PENDING_APPROVAL", "APPROVED", "RUNNING"}:
                    raise TaskRunConflict("Completed tasks cannot be cancelled")
                conn.execute(
                    "UPDATE synthetic_task_runs SET status='CANCELLED',"
                    "failure_code='cancelled_by_operator',result_json=NULL,"
                    "lease_token=NULL,lease_deadline=NULL,version=version+1,updated_at=? "
                    "WHERE run_id=?",
                    (now, run_id),
                )
                updated = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                conn.commit()
                return self._public(updated)
            except Exception:
                conn.rollback()
                raise

    def claim(self, run_id: str, lease_seconds: int = 60) -> tuple[dict[str, Any], str]:
        """Atomic claim; expired workers cannot commit their old lease token."""
        self._validate_run_id(run_id)
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 600:
            raise ValueError("Invalid lease budget")
        now = time.time()
        token = secrets.token_hex(16)
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise TaskRunNotFound("TaskRun not found")
                eligible = row["status"] == "APPROVED" or (
                    row["status"] == "RUNNING" and row["lease_deadline"] is not None and row["lease_deadline"] < now
                )
                if not eligible:
                    raise TaskRunConflict("TaskRun is not claimable")
                if row["attempts"] >= _MAX_ATTEMPTS:
                    # An expired final attempt must not remain RUNNING forever:
                    # it would permanently consume the owner's active-task quota.
                    # Invalidate the old lease atomically before returning conflict.
                    conn.execute(
                        "UPDATE synthetic_task_runs SET status='FAILED',"
                        "failure_code='recovery_budget_exhausted',"
                        "lease_token=NULL,lease_deadline=NULL,version=version+1,updated_at=? "
                        "WHERE run_id=?",
                        (now, run_id),
                    )
                    conn.commit()
                    raise TaskRunConflict("TaskRun exhausted its recovery budget")
                conn.execute(
                    "UPDATE synthetic_task_runs SET status='RUNNING',attempts=attempts+1,"
                    "lease_token=?,lease_deadline=?,version=version+1,updated_at=? WHERE run_id=?",
                    (token, now + lease_seconds, now, run_id),
                )
                updated = conn.execute("SELECT * FROM synthetic_task_runs WHERE run_id=?", (run_id,)).fetchone()
                conn.commit()
                public = self._public(updated)
                public["payload"] = json.loads(updated["payload_json"])
                return public, token
            except Exception:
                conn.rollback()
                raise

    def reconcile_exhausted(self, max_runs: int = 100) -> int:
        """Terminalize expired final-attempt leases even when no worker reclaims them.

        This explicit maintenance operation is bounded and idempotent. A
        still-live lease and an expired first attempt remain untouched.
        """
        if type(max_runs) is not int or not 1 <= max_runs <= 200:
            raise ValueError("Maintenance batch size must be between 1 and 200")
        now = time.time()
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT run_id FROM synthetic_task_runs "
                    "WHERE status='RUNNING' AND attempts>=? AND lease_deadline<? "
                    "ORDER BY lease_deadline,run_id LIMIT ?",
                    (_MAX_ATTEMPTS, now, max_runs),
                ).fetchall()
                if not rows:
                    conn.commit()
                    return 0
                ids = [row["run_id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                update = conn.execute(
                    "UPDATE synthetic_task_runs SET status='FAILED',"
                    "failure_code='recovery_budget_exhausted',"
                    "lease_token=NULL,lease_deadline=NULL,version=version+1,updated_at=? "
                    f"WHERE status='RUNNING' AND attempts>=? AND lease_deadline<? "
                    f"AND run_id IN ({placeholders})",
                    (now, _MAX_ATTEMPTS, now, *ids),
                )
                if update.rowcount != len(ids):
                    raise TaskRunConflict("Lease reconciliation changed unexpectedly")
                conn.commit()
                return update.rowcount
            except Exception:
                conn.rollback()
                raise

    def assert_active_lease(self, run_id: str, lease_token: str) -> None:
        """Cooperative cancellation check before/after potentially expensive work.

        This is NOT a hard interrupt: a blocking provider request may finish
        before the next check. Do not return the lease token to API clients.
        """
        self._validate_run_id(run_id)
        if not isinstance(lease_token, str) or not re.fullmatch(r"[0-9a-f]{32}", lease_token):
            raise ValueError("Invalid lease token")
        with self._open() as conn:
            row = conn.execute(
                "SELECT 1 FROM synthetic_task_runs "
                "WHERE run_id=? AND status='RUNNING' AND lease_token=? AND lease_deadline>?",
                (run_id, lease_token, time.time()),
            ).fetchone()
        if row is None:
            raise TaskRunConflict("TaskRun lease no longer active")

    def finish(
        self, run_id: str, lease_token: str, status: str, result: dict[str, Any] | None, failure_code: str | None
    ) -> dict[str, Any]:
        self._validate_run_id(run_id)
        if status not in {"SUCCEEDED", "NEEDS_REVIEW", "FAILED"}:
            raise ValueError("Unsupported terminal status")
        if result is not None and (
            not isinstance(result, dict)
            or result.get("data_profile") != "SYNTHETIC_DEMO"
            or len(_canonical(result)) > 30000
        ):
            raise ValueError("Invalid or unsafe result")
        if failure_code is not None and (
            not isinstance(failure_code, str) or not re.fullmatch(r"[a-z_]{3,60}", failure_code)
        ):
            raise ValueError("Invalid failure code")
        now = time.time()
        with self._open() as conn:
            updated = conn.execute(
                "UPDATE synthetic_task_runs SET status=?,result_json=?,failure_code=?,"
                "lease_token=NULL,lease_deadline=NULL,version=version+1,updated_at=? "
                "WHERE run_id=? AND status='RUNNING' AND lease_token=? AND lease_deadline>?",
                (
                    status,
                    _canonical(result) if result is not None else None,
                    failure_code,
                    now,
                    run_id,
                    lease_token,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise TaskRunConflict("Lost lease: stale worker cannot commit")
        return self.get(run_id)
