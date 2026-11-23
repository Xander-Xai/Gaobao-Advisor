"""Opt-in, reviewed negative-rule memory for SYNTHETIC_DEMO research experiments.

Not connected to public /chat and not an identity, RBAC or provenance service.
No arbitrary model text is stored or injected. Callers MUST authenticate both
owner and reviewer before invoking this interface. A source_run_id has format
validation only; this class does not establish that its business receipt exists.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

ALLOWED_RULES = frozenset(
    {
        "DENY_UNVERIFIED_ADMISSIONS_SOURCE",
        "DENY_ADMISSION_PROBABILITY_CLAIMS",
        "REQUIRE_REVIEW_ON_SQL_CONFLICT",
    }
)
_ID = re.compile(r"^[a-zA-Z0-9_.:-]{3,80}$")
_RUN_ID = re.compile(r"^syn_[0-9a-f]{32}$")
_MAX_TTL = 30 * 24 * 3600


class NegativeMemoryConflict(ValueError):
    """The requested transition is invalid or not authorized."""


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise NegativeMemoryConflict("Invalid opaque owner or operator ID")
    return value


def _rule(value: str) -> str:
    if not isinstance(value, str) or value not in ALLOWED_RULES:
        raise NegativeMemoryConflict("Unapproved negative-rule identifier")
    return value


class SyntheticNegativeRuleMemory:
    """A small SQLite lifecycle demonstrating reviewed deny-rule retention.

    This is intentionally separate from TaskRun and cross-task user-profile
    memory. It provides no production cross-tenant authorization itself.
    """

    def __init__(self, path: Path, *, clock: Callable[[], int] | None = None) -> None:
        path = Path(path)
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("An absolute path under an existing directory is required")
        self.path = path
        self.clock = clock or (lambda: int(time.time()))
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS synthetic_negative_rules (
                    owner TEXT NOT NULL,
                    rule_key TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    proposed_by TEXT NOT NULL,
                    reviewed_by TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED', 'REVOKED')),
                    expires_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(owner, rule_key)
                );
                CREATE TABLE IF NOT EXISTS synthetic_negative_rule_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL,
                    rule_key TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                );
                """
            )

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def propose(
        self,
        *,
        owner: str,
        rule_key: str,
        source_run_id: str,
        proposed_by: str,
        ttl_seconds: int,
    ) -> None:
        owner, proposed_by, rule_key = _id(owner), _id(proposed_by), _rule(rule_key)
        if not isinstance(source_run_id, str) or not _RUN_ID.fullmatch(source_run_id):
            raise NegativeMemoryConflict("A synthetic TaskRun identity is required")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL:
            raise NegativeMemoryConflict("TTL must be between 1 second and 30 days")
        now = self.clock()
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT status, expires_at FROM synthetic_negative_rules WHERE owner=? AND rule_key=?",
                (owner, rule_key),
            ).fetchone()
            if prior is not None and prior["expires_at"] > now:
                raise NegativeMemoryConflict("An unexpired rule already exists")
            if prior is not None:
                conn.execute(
                    "DELETE FROM synthetic_negative_rules WHERE owner=? AND rule_key=?",
                    (owner, rule_key),
                )
            conn.execute(
                """
                INSERT INTO synthetic_negative_rules
                  (owner, rule_key, source_run_id, proposed_by, status, expires_at, updated_at)
                VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (owner, rule_key, source_run_id, proposed_by, now + ttl_seconds, now),
            )
            self._audit(conn, owner, rule_key, proposed_by, "PROPOSED", now)

    def review(
        self,
        *,
        owner: str,
        rule_key: str,
        reviewed_by: str,
        approve: bool,
        reviewer_authorized: bool = False,
    ) -> None:
        owner, reviewed_by, rule_key = _id(owner), _id(reviewed_by), _rule(rule_key)
        if reviewer_authorized is not True or type(approve) is not bool:
            raise NegativeMemoryConflict("Trusted reviewer authorization is required")
        now = self.clock()
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = conn.execute(
                "SELECT proposed_by, status, expires_at FROM synthetic_negative_rules "
                "WHERE owner=? AND rule_key=?",
                (owner, rule_key),
            ).fetchone()
            if record is None or record["status"] != "PENDING" or record["expires_at"] <= now:
                raise NegativeMemoryConflict("No live pending rule to review")
            if record["proposed_by"] == reviewed_by:
                raise NegativeMemoryConflict("Proposer cannot approve own rule")
            result = conn.execute(
                "UPDATE synthetic_negative_rules SET status=?, reviewed_by=?, updated_at=? "
                "WHERE owner=? AND rule_key=? AND status='PENDING'",
                ("APPROVED" if approve else "REJECTED", reviewed_by, now, owner, rule_key),
            )
            if result.rowcount != 1:
                raise NegativeMemoryConflict("Concurrent review conflict")
            self._audit(conn, owner, rule_key, reviewed_by, "APPROVED" if approve else "REJECTED", now)

    def active_rules(self, *, owner: str) -> tuple[str, ...]:
        owner = _id(owner)
        with self._db() as conn:
            rows = conn.execute(
                "SELECT rule_key FROM synthetic_negative_rules "
                "WHERE owner=? AND status='APPROVED' AND expires_at>? ORDER BY rule_key",
                (owner, self.clock()),
            ).fetchall()
        return tuple(row["rule_key"] for row in rows if row["rule_key"] in ALLOWED_RULES)

    def revoke(
        self, *, owner: str, rule_key: str, actor: str, reviewer_authorized: bool = False
    ) -> None:
        owner, rule_key, actor = _id(owner), _rule(rule_key), _id(actor)
        if reviewer_authorized is not True:
            raise NegativeMemoryConflict("Trusted reviewer authorization is required")
        now = self.clock()
        with self._db() as conn:
            result = conn.execute(
                "UPDATE synthetic_negative_rules SET status='REVOKED', updated_at=? "
                "WHERE owner=? AND rule_key=? AND status='APPROVED'",
                (now, owner, rule_key),
            )
            if result.rowcount != 1:
                raise NegativeMemoryConflict("No approved rule to revoke")
            self._audit(conn, owner, rule_key, actor, "REVOKED", now)

    @staticmethod
    def _audit(conn: sqlite3.Connection, owner: str, rule: str, actor: str, event: str, now: int) -> None:
        conn.execute(
            "INSERT INTO synthetic_negative_rule_audit (owner, rule_key, actor, transition, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner, rule, actor, event, now),
        )
