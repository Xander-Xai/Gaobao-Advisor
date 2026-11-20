"""Transactional SQLite checkpoint store for *synthetic read-only* Agent runs.

This is a single-file local development checkpoint, not a distributed lease
service or a production identity/memory database. Snapshots can contain tool
observations: never pass personal data or credentials into this experiment.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^synthetic-run-[a-zA-Z0-9_-]{3,72}$")


def task_digest(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


class CheckpointConflict(RuntimeError):
    """Raised when another writer has already advanced the same run."""


class SQLiteSyntheticCheckpointStore:
    """Optimistic CAS and exact task binding prevent silent run corruption."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.parent.is_dir():
            raise ValueError("Checkpoint directory must exist")
        with self._open() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_agent_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    task_sha256 TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                )
                """
            )

    def _open(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=2.0, isolation_level=None)

    @staticmethod
    def _validate(run_id: str, digest: str) -> None:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("Only synthetic-run-* IDs are accepted")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("A SHA-256 task digest is required")

    def load(self, run_id: str, digest: str) -> tuple[int, dict[str, Any]] | None:
        self._validate(run_id, digest)
        with self._open() as conn:
            row = conn.execute(
                "SELECT task_sha256, version, state_json FROM synthetic_agent_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != digest:
            raise ValueError("Run ID reused for a different task")
        snapshot = json.loads(row[2])
        if not isinstance(snapshot, dict) or snapshot.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Checkpoint contains invalid synthetic data profile")
        return int(row[1]), snapshot

    def save(self, run_id: str, digest: str, expected_version: int | None, snapshot: dict[str, Any]) -> int:
        self._validate(run_id, digest)
        if not isinstance(snapshot, dict) or snapshot.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Only synthetic checkpoint snapshots are permitted")
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if len(encoded) > 32000:
            raise ValueError("Checkpoint size budget exceeded")
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT task_sha256, version FROM synthetic_agent_checkpoints WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                if expected_version is not None:
                    raise CheckpointConflict("Expected an existing run")
                new_version = 0
                conn.execute(
                    "INSERT INTO synthetic_agent_checkpoints (run_id, task_sha256, version, state_json) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, digest, new_version, encoded),
                )
            else:
                if row[0] != digest:
                    raise ValueError("Run ID reused for a different task")
                if type(expected_version) is not int or row[1] != expected_version:
                    raise CheckpointConflict("Concurrent or stale checkpoint writer")
                new_version = expected_version + 1
                conn.execute(
                    "UPDATE synthetic_agent_checkpoints SET version=?, state_json=? WHERE run_id=?",
                    (new_version, encoded, run_id),
                )
            conn.commit()
            return new_version
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
