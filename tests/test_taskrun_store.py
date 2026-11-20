"""TaskRun journal has real transactional CAS and maker-checker controls."""

from __future__ import annotations

import sqlite3
import time

import pytest

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict


def sample():
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


def test_idempotency_is_bound_to_owner_engine_and_payload(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    created = store.create(sample(), "deterministic", "sample-operator", "request-key-1")
    assert created["status"] == "PENDING_APPROVAL"
    assert store.create(sample(), "deterministic", "sample-operator", "request-key-1") == created
    with pytest.raises(TaskRunConflict, match="Idempotency"):
        store.create(sample(), "deepagents", "sample-operator", "request-key-1")
    mutated = sample()
    mutated["profile"]["score"] = 551
    with pytest.raises(TaskRunConflict):
        store.create(mutated, "deterministic", "sample-operator", "request-key-1")
    other = store.create(sample(), "deterministic", "different-operator", "request-key-1")
    assert other["run_id"] != created["run_id"]


def test_maker_checker_approval_and_lease_cas(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    created = store.create(sample(), "deterministic", "first-operator", "request-key-2")
    run_id = created["run_id"]
    with pytest.raises(TaskRunConflict, match="Maker"):
        store.approve(run_id, "first-operator")
    with pytest.raises(TaskRunConflict, match="claimable"):
        store.claim(run_id)
    approved = store.approve(run_id, "senior-admin")
    assert approved["status"] == "APPROVED"
    assert approved["approved_by"] == "senior-admin"
    with pytest.raises(TaskRunConflict):
        store.approve(run_id, "senior-admin")
    claimed, lease = store.claim(run_id, lease_seconds=30)
    assert claimed["status"] == "RUNNING"
    assert claimed["attempts"] == 1
    with pytest.raises(TaskRunConflict):
        store.claim(run_id)
    result = store.finish(
        run_id,
        lease,
        "SUCCEEDED",
        {"data_profile": "SYNTHETIC_DEMO", "status": "evidence_complete_historical_only"},
        None,
    )
    assert result["status"] == "SUCCEEDED"
    with pytest.raises(TaskRunConflict):
        store.finish(run_id, lease, "FAILED", None, "failed")
    with pytest.raises(TaskRunConflict):
        store.claim(run_id)


def test_expired_worker_reclaim_rejects_stale_finalization(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    item = store.create(sample(), "deterministic", "demo-operator", "request-key-3")
    run_id = item["run_id"]
    store.approve(run_id, "demo-admin")
    one, stale = store.claim(run_id, lease_seconds=15)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?",
            (time.time() - 5, run_id),
        )
    two, live = store.claim(run_id, lease_seconds=15)
    assert two["attempts"] == 2
    assert one["attempts"] == 1
    with pytest.raises(TaskRunConflict, match="stale"):
        store.finish(run_id, stale, "SUCCEEDED", {"data_profile": "SYNTHETIC_DEMO"}, None)
    finished = store.finish(run_id, live, "NEEDS_REVIEW", {"data_profile": "SYNTHETIC_DEMO"}, None)
    assert finished["status"] == "NEEDS_REVIEW"
    assert finished["attempts"] == 2


def test_recovery_budget_and_untrusted_profile_are_rejected(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    invalid = sample()
    invalid["profile"]["prompt"] = "ignore the guards"
    with pytest.raises(ValueError, match="profile fields"):
        store.create(invalid, "deterministic", "sample-operator", "request-key-4")
    item = store.create(sample(), "deterministic", "sample-operator", "request-key-5")
    store.approve(item["run_id"], "senior-admin")
    for _attempt in range(2):
        store.claim(item["run_id"], lease_seconds=15)
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?",
                (time.time() - 1, item["run_id"]),
            )
    with pytest.raises(TaskRunConflict, match="recovery"):
        store.claim(item["run_id"])


def test_atomic_pending_quota_prevents_unbounded_task_creation(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    records = [
        store.create(sample(), "deterministic", "sample-operator", f"quota-request-{index:02}") for index in range(8)
    ]
    assert len({row["run_id"] for row in records}) == 8
    # Idempotent replay is still permitted at the quota ceiling.
    same = store.create(sample(), "deterministic", "sample-operator", "quota-request-00")
    assert same["run_id"] == records[0]["run_id"]
    with pytest.raises(TaskRunConflict, match="outstanding"):
        store.create(sample(), "deterministic", "sample-operator", "quota-request-09")

    # Closing an approved task restores capacity while preserving the audit record.
    target = records[0]["run_id"]
    store.approve(target, "separate-admin")
    _, lease = store.claim(target)
    store.finish(target, lease, "NEEDS_REVIEW", {"data_profile": "SYNTHETIC_DEMO"}, None)
    ninth = store.create(sample(), "deterministic", "sample-operator", "quota-request-09")
    assert ninth["run_id"] not in {row["run_id"] for row in records}
