"""Task cancellation revokes publication and restores quota, not OS execution."""

from __future__ import annotations

import sqlite3
import time

import pytest

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict, TaskRunNotFound
from tests.conftest import auth_header, login

PATH = "/api/v1/admin/agent-taskruns"


def profile():
    return {
        "school_a": DEMO_SCHOOLS[0][0],
        "school_b": DEMO_SCHOOLS[6][0],
        "major": DEMO_MAJORS[0][0],
        "province": "示例省·甲",
        "score": 550,
        "subject_type": "物理类",
        "year": DEMO_YEAR,
    }


def payload():
    return {"data_profile": "SYNTHETIC_DEMO", "profile": profile()}


def test_pending_approved_and_running_cancelled_without_result_publication(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    for status in ("PENDING_APPROVAL", "APPROVED", "RUNNING"):
        created = store.create(payload(), "deterministic", "demo-owner", "cancel-" + status.lower() + "-2026")
        run = created["run_id"]
        if status in ("APPROVED", "RUNNING"):
            store.approve(run, "separate-admin")
        if status == "RUNNING":
            _, token = store.claim(run)
        cancelled = store.cancel(run, "demo-owner")
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["failure_code"] == "cancelled_by_operator"
        assert cancelled["result"] is None
        assert store.cancel(run, "demo-owner")["version"] == cancelled["version"]
        with pytest.raises(TaskRunConflict, match="claimable"):
            store.claim(run)
        if status == "RUNNING":
            with pytest.raises(TaskRunConflict, match="stale"):
                store.finish(run, token, "SUCCEEDED", {"data_profile": "SYNTHETIC_DEMO"}, None)
            with sqlite3.connect(store.path) as conn:
                row = conn.execute(
                    "SELECT lease_token,lease_deadline FROM synthetic_task_runs WHERE run_id=?", (run,)
                ).fetchone()
                assert row == (None, None)


def test_cancel_is_owner_scoped_and_never_reverses_completed_results(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    created = store.create(payload(), "deterministic", "demo-owner", "cancel-owned-2026")
    run = created["run_id"]
    with pytest.raises(TaskRunNotFound):
        store.cancel(run, "other-user")
    assert store.cancel(run, "separate-admin", admin=True)["status"] == "CANCELLED"

    completed = store.create(payload(), "deterministic", "demo-owner", "cancel-terminal-2026")
    store.approve(completed["run_id"], "separate-admin")
    _, lease = store.claim(completed["run_id"])
    store.finish(completed["run_id"], lease, "NEEDS_REVIEW", {"data_profile": "SYNTHETIC_DEMO"}, None)
    with pytest.raises(TaskRunConflict, match="Completed"):
        store.cancel(completed["run_id"], "demo-owner")

    # Cancellation restores the active-task quota, and idempotent replays remain cancelled.
    for n in range(8):
        store.create(payload(), "deterministic", "demo-owner", f"quota-cancel-{n:02}")
    with pytest.raises(TaskRunConflict, match="quota"):
        store.create(payload(), "deterministic", "demo-owner", "quota-cancel-08")
    one = store.get(created["run_id"])
    assert one["status"] == "CANCELLED"
    assert store.cancel(created["run_id"], "demo-owner")["status"] == "CANCELLED"


def test_cancelling_active_task_releases_exactly_one_owner_quota_slot(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    ids = [
        store.create(payload(), "deterministic", "demo-owner", f"quota-cancel-{n:02}")["run_id"]
        for n in range(8)
    ]
    with pytest.raises(TaskRunConflict, match="quota"):
        store.create(payload(), "deterministic", "demo-owner", "quota-cancel-08")
    store.cancel(ids[0], "demo-owner")
    ninth = store.create(payload(), "deterministic", "demo-owner", "quota-cancel-08")
    assert ninth["status"] == "PENDING_APPROVAL"
    assert ninth["run_id"] not in ids


def test_api_cancel_authorization_and_state_machine(admin_client, monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("SYNTHETIC_TASKRUN_JOURNAL", str(tmp_path / "taskrun.db"))
    operator = auth_header(login(admin_client, "operator"))
    admin = auth_header(login(admin_client, "admin"))
    viewer = auth_header(login(admin_client, "viewer"))
    body = {"profile": profile(), "engine": "deterministic", "idempotency_key": "api-cancel-2026"}
    created = admin_client.post(f"{PATH}/runs", json=body, headers=operator)
    assert created.status_code == 201
    run = created.json()["run_id"]
    assert admin_client.post(f"{PATH}/runs/{run}/cancel").status_code == 401
    assert admin_client.post(f"{PATH}/runs/{run}/cancel", headers=viewer).status_code == 403
    denied = admin_client.post(f"{PATH}/runs/syn_{'f' * 32}/cancel", headers=operator)
    assert denied.status_code == 404
    response = admin_client.post(f"{PATH}/runs/{run}/cancel", headers=operator)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert admin_client.post(f"{PATH}/runs/{run}/approve", headers=admin).status_code == 409
    assert admin_client.post(f"{PATH}/runs/{run}/execute", headers=operator).status_code == 409
    assert admin_client.get(f"{PATH}/runs/{run}", headers=operator).json()["status"] == "CANCELLED"


def test_expired_cancelled_lease_cannot_be_resurrected_by_reconciliation(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.db")
    row = store.create(payload(), "deterministic", "demo-owner", "cancel-expired-2026")
    store.approve(row["run_id"], "separate-admin")
    _, token = store.claim(row["run_id"], lease_seconds=15)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?",
            (time.time() - 5, row["run_id"]),
        )
    store.cancel(row["run_id"], "demo-owner")
    assert store.reconcile_exhausted() == 0
    with pytest.raises(TaskRunConflict):
        store.finish(row["run_id"], token, "FAILED", None, "agent_execution_failed")
    assert store.get(row["run_id"])["status"] == "CANCELLED"
