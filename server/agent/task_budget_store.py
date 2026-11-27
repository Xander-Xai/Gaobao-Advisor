"""Durable storage for a task-level resource budget.

Two lifetimes need two different stores:

* **Synchronous request.**  The budget lives in memory for the duration of
  the request and dies with it.  No persistence, no recovery.

* **Persisted asynchronous task.**  The budget must survive the process that
  created it.  A TaskRun whose worker crashed and whose lease expired is
  reclaimed and re-executed; the reclaimed task must continue with the
  budget it had *already* consumed, otherwise a crash loop would hand out a
  fresh allowance on every attempt.

The SQLite store below keeps the ledger snapshot next to the task journal so
both are committed by the same database.  The snapshot is written on every
settlement, so the persisted consumption is never older than the last
completed provider attempt.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "InMemoryTaskBudgetStore",
    "SqliteTaskBudgetStore",
    "TaskBudgetStore",
    "bind_task_budget_persistence",
]


class TaskBudgetStore:
    """Interface for a task budget snapshot store."""

    def load(self, scope_key: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def save(self, scope_key: str, snapshot: dict[str, Any]) -> None:
        raise NotImplementedError

    def clear(self, scope_key: str) -> None:
        raise NotImplementedError


class InMemoryTaskBudgetStore(TaskBudgetStore):
    """Non-durable store, used by the synchronous request path."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def load(self, scope_key: str) -> dict[str, Any] | None:
        snapshot = self._data.get(scope_key)
        return json.loads(json.dumps(snapshot)) if snapshot is not None else None

    def save(self, scope_key: str, snapshot: dict[str, Any]) -> None:
        self._data[scope_key] = json.loads(json.dumps(snapshot))

    def clear(self, scope_key: str) -> None:
        self._data.pop(scope_key, None)


class SqliteTaskBudgetStore(TaskBudgetStore):
    """SQLite-backed store that survives a process crash.

    The table is created lazily and lives in the same database file as the
    owning journal, so a snapshot and the task state commit together.
    """

    def __init__(self, path: str | Path, *, table: str = "task_budgets", timeout: float = 5.0) -> None:
        original = Path(path)
        if not original.is_absolute():
            raise ValueError("Task budget store path must be absolute")
        self.path = original.resolve()
        if not self.path.parent.is_dir():
            raise ValueError("Task budget store requires an existing directory")
        if not table.replace("_", "").isalnum():
            raise ValueError("Invalid task budget table name")
        self.table = table
        self._timeout = timeout
        self._ensure_table()

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self._timeout, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        with self._open() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    scope_key TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def load(self, scope_key: str) -> dict[str, Any] | None:
        if not isinstance(scope_key, str) or not scope_key:
            raise ValueError("Invalid task budget scope key")
        with self._open() as conn:
            row = conn.execute(
                f"SELECT snapshot_json FROM {self.table} WHERE scope_key=?", (scope_key,)
            ).fetchone()
        if row is None:
            return None
        try:
            snapshot = json.loads(row["snapshot_json"])
        except json.JSONDecodeError:
            # A corrupt snapshot must not silently reset the budget to zero.
            raise ValueError("Corrupt task budget snapshot") from None
        if not isinstance(snapshot, dict):
            raise ValueError("Corrupt task budget snapshot")
        return snapshot

    def save(self, scope_key: str, snapshot: dict[str, Any]) -> None:
        if not isinstance(scope_key, str) or not scope_key:
            raise ValueError("Invalid task budget scope key")
        if not isinstance(snapshot, dict):
            raise ValueError("Task budget snapshot must be a mapping")
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._open() as conn:
            conn.execute(
                f"INSERT INTO {self.table} (scope_key,snapshot_json,updated_at) VALUES (?,?,?) "
                f"ON CONFLICT(scope_key) DO UPDATE SET snapshot_json=excluded.snapshot_json, "
                f"updated_at=excluded.updated_at",
                (scope_key, payload, time.time()),
            )

    def clear(self, scope_key: str) -> None:
        if not isinstance(scope_key, str) or not scope_key:
            raise ValueError("Invalid task budget scope key")
        with self._open() as conn:
            conn.execute(f"DELETE FROM {self.table} WHERE scope_key=?", (scope_key,))


def bind_task_budget_persistence(
    budget: Any,
    store: TaskBudgetStore,
    scope_key: str,
) -> Any:
    """Persist *budget* into *store* under *scope_key* on every settlement.

    The callback is invoked from inside the ledger lock, so it must be
    cheap and must not re-enter the budget.  A failing persistence callback
    is logged by the ledger rather than raised into the agent loop.
    """
    if not isinstance(scope_key, str) or not scope_key:
        raise ValueError("Invalid task budget scope key")

    def _persist(ledger: Any) -> None:
        store.save(scope_key, ledger.to_snapshot())

    budget.bind_persistence(on_settle=_persist)
    return budget
