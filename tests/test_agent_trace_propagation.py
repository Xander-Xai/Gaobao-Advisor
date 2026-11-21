"""Private TaskRun trace inheritance, W3C parsing and attribute allowlists."""

from __future__ import annotations

import sqlite3

import pytest

from server.agent.taskrun_store import SyntheticTaskRunStore
from server.observability.agent_trace import (
    continue_trace,
    current_traceparent,
    safe_agent_span,
    safe_attributes,
    valid_traceparent,
)
from tests.test_taskrun_store import sample

PARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


def test_observability_ignores_sensitive_and_unbounded_tags():
    tags = safe_attributes(
        {
            "http.method": "POST",
            "http.route_group": "agent_admin",
            "agent.engine": "portfolio",
            "agent.tool": "admission_lookup",
            "agent.tool_calls": 2,
            "prompt": "secret student",
            "request_id": "secret-id",
            "exception": "api-key-secret",
        }
    )
    assert tags == {
        "http.method": "POST",
        "http.route_group": "agent_admin",
        "agent.engine": "portfolio",
        "agent.tool": "admission_lookup",
        "agent.tool_calls": 2,
    }
    assert safe_attributes({"agent.engine": "not-in-allowlist", "agent.tool_calls": -3}) == {"agent.engine": "unknown"}


@pytest.mark.parametrize(
    "value",
    [
        "00-" + "0" * 32 + "-" + "b" * 16 + "-01",
        "00-" + "a" * 32 + "-" + "0" * 16 + "-01",
        "00-" + "a" * 32 + "-" + "b" * 16 + "-01-extra",
        "ff-" + "a" * 32 + "-" + "b" * 16 + "-01",
        "password:secret",
        None,
    ],
)
def test_reject_invalid_traceparent(value):
    assert not valid_traceparent(value)


def test_span_cannot_export_arbitrary_name():
    assert valid_traceparent(PARENT)
    with continue_trace(PARENT):
        with safe_agent_span("agent.taskrun", {"agent.engine": "portfolio"}):
            observed = current_traceparent()
            assert observed is None or valid_traceparent(observed)
    with pytest.raises(ValueError, match="Unsupported"):
        with safe_agent_span("dump.private.prompt"):
            pass


def test_taskrun_traceparent_is_private_and_durable(tmp_path, monkeypatch):
    monkeypatch.setattr("server.agent.taskrun_store.current_traceparent", lambda: PARENT)
    journal = tmp_path / "synthetic-trace.sqlite"
    store = SyntheticTaskRunStore(journal)
    task = store.create(sample(), "deterministic", "operator-example", "trace-test-01")
    assert "traceparent" not in task
    reopened = SyntheticTaskRunStore(journal)
    assert reopened.get_internal_traceparent(task["run_id"]) == PARENT
    assert "traceparent" not in reopened.get(task["run_id"])
    with sqlite3.connect(journal) as conn:
        conn.execute(
            "UPDATE synthetic_task_runs SET traceparent=? WHERE run_id=?",
            ("00-" + "0" * 32 + "-" + "b" * 16 + "-01", task["run_id"]),
        )
    assert reopened.get_internal_traceparent(task["run_id"]) is None


def test_existing_taskrun_table_is_migrated(tmp_path):
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE synthetic_task_runs (run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "engine TEXT NOT NULL, status TEXT NOT NULL, approved_by TEXT, "
            "version INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, "
            "lease_token TEXT, lease_deadline REAL, result_json TEXT, failure_code TEXT, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(owner,idempotency_key))"
        )
    SyntheticTaskRunStore(db)
    with sqlite3.connect(db) as conn:
        names = [row[1] for row in conn.execute("PRAGMA table_info(synthetic_task_runs)")]
    assert "traceparent" in names
