"""Bounded, independently invokable cleanup of abandoned final-attempt leases."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict


def _payload():
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "profile": {
            "school_a": DEMO_SCHOOLS[0][0],
            "school_b": DEMO_SCHOOLS[6][0],
            "major": DEMO_MAJORS[0][0],
            "province": "示例省·甲",
            "score": 550,
            "subject_type": "物理类",
            "year": DEMO_YEAR,
        },
    }


def _expired_run(store, key, attempts):
    row = store.create(_payload(), "deterministic", "eval-operator", key)
    store.approve(row["run_id"], "different-admin")
    last_token = None
    for _ in range(attempts):
        _, last_token = store.claim(row["run_id"], lease_seconds=15)
        with sqlite3.connect(store.path) as db:
            db.execute(
                "UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?",
                (time.time() - 5, row["run_id"]),
            )
    return row["run_id"], last_token


def test_reconciliation_is_bounded_atomic_idempotent_and_releases_quota(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "task-journal.sqlite")
    first, stale = _expired_run(store, "reconcile-expired-first", 2)
    second, _ = _expired_run(store, "reconcile-expired-second", 2)
    eligible, _ = _expired_run(store, "reconcile-one-attempt", 1)
    live = store.create(_payload(), "deterministic", "eval-operator", "reconcile-not-expired")
    store.approve(live["run_id"], "different-admin")
    store.claim(live["run_id"], lease_seconds=60)
    pending = store.create(_payload(), "deterministic", "eval-operator", "reconcile-still-pending")

    assert store.reconcile_exhausted(max_runs=1) == 1
    assert store.get(first)["status"] == "FAILED"
    assert store.get(first)["failure_code"] == "recovery_budget_exhausted"
    assert store.get(second)["status"] == "RUNNING"
    assert store.reconcile_exhausted(max_runs=1) == 1
    assert store.reconcile_exhausted(max_runs=1) == 0
    assert store.get(second)["status"] == "FAILED"
    assert store.get(eligible)["status"] == "RUNNING"
    assert store.get(live["run_id"])["status"] == "RUNNING"
    assert store.get(pending["run_id"])["status"] == "PENDING_APPROVAL"
    with pytest.raises(TaskRunConflict, match="stale"):
        store.finish(first, stale, "SUCCEEDED", {"data_profile": "SYNTHETIC_DEMO"}, None)
    with pytest.raises(ValueError):
        store.reconcile_exhausted(max_runs=0)
    with pytest.raises(ValueError):
        store.reconcile_exhausted(max_runs=True)
    created = store.create(_payload(), "deterministic", "eval-operator", "reconcile-new-slot")
    assert created["status"] == "PENDING_APPROVAL"


def test_maintenance_cli_requires_safe_environment_and_never_calls_model(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "task-journal.sqlite")
    expired, _ = _expired_run(store, "cli-exhausted-task", 2)
    command = [
        sys.executable,
        "-m",
        "server.agent.taskrun_maintenance",
        "--journal",
        str(store.path),
        "--max-runs",
        "1",
    ]
    env = dict(os.environ)
    env["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    env["ENABLE_OUTBOUND_FETCH"] = "false"
    env["APP_ENV"] = "production"
    denied = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30, env=env)
    assert denied.returncode != 0
    assert store.get(expired)["status"] == "RUNNING"

    env["APP_ENV"] = "development"
    allowed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30, env=env)
    assert allowed.returncode == 0, allowed.stderr
    receipt = json.loads(allowed.stdout)
    assert receipt["terminalized_runs"] == 1
    assert receipt["external_model_executed"] is False
    assert store.get(expired)["status"] == "FAILED"
    repeated = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30, env=env)
    assert repeated.returncode == 0
    assert json.loads(repeated.stdout)["terminalized_runs"] == 0
