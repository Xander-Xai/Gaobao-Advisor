"""Cooperative TaskRun cancellation prevents post-cancel work and result writes.

Injected runner is a fake, not an external LLM. Fresh SQLite per subprocess.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SOURCE = r"""
import os
import sqlite3
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "synthetic.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["OPENAI_API_KEY"] = "mock-only-not-a-provider-secret"

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
seed_database(db_path=root / "synthetic.sqlite", reset=True)
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict
from server.agent.taskrun_worker import execute_approved_run
from server.agent.synthetic_decision_card import build_synthetic_decision_card
import server.agent.taskrun_worker as worker

store = SyntheticTaskRunStore(root / "journal.sqlite")
profile = {
    "school_a": DEMO_SCHOOLS[0][0],
    "school_b": DEMO_SCHOOLS[6][0],
    "major": DEMO_MAJORS[0][0],
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
}
payload = {"data_profile":"SYNTHETIC_DEMO","profile":profile}
def new(key):
    task = store.create(payload, "deepagents", "cancellation-owner", key)
    store.approve(task["run_id"], "different-reviewer")
    return task["run_id"]

verification_calls = []
original_verify = worker.verify_taskrun_receipt
def counting_verify(p, card):
    verification_calls.append("called")
    return original_verify(p, card)
worker.verify_taskrun_receipt = counting_verify

run = new("cancel-after-runner-2026")
def returning_runner(p, model, *, task_id):
    assert task_id == run
    assert model == "openai:fictional-test"
    cancelled = store.cancel(run, "cancellation-owner")
    assert cancelled["status"] == "CANCELLED"
    return build_synthetic_decision_card(p)
outcome = execute_approved_run(store, run, permit_external_model=True, model="openai:fictional-test", agent_runner=returning_runner)
assert outcome["status"] == "CANCELLED", outcome
assert outcome["result"] is None
assert verification_calls == []
assert store.get(run)["status"] == "CANCELLED"

# A second thread cancels while the fake model request is suspended.
# The model finishes later; it must not reach the SQL receipt verifier.
import threading

run = new("cancel-during-inflight-call-2026")
entered = threading.Event()
release = threading.Event()
thread_outcome = []
thread_failure = []
def blocked_runner(p, model, *, task_id):
    entered.set()
    assert release.wait(10)
    return {"data_profile": "SYNTHETIC_DEMO", "untrusted": True}
def run_blocked():
    try:
        thread_outcome.append(
            execute_approved_run(
                store, run, permit_external_model=True,
                model="openai:fictional-test", agent_runner=blocked_runner
            )
        )
    except Exception as exc:
        thread_failure.append(type(exc).__name__)
thread = threading.Thread(target=run_blocked)
thread.start()
assert entered.wait(10)
store.cancel(run, "cancellation-owner")
release.set()
thread.join(10)
assert not thread.is_alive()
assert not thread_failure, thread_failure
assert thread_outcome[0]["status"] == "CANCELLED"
assert thread_outcome[0]["result"] is None
assert verification_calls == []

run = new("cancel-and-exception-2026")
def failing_runner(p, model, *, task_id):
    store.cancel(run, "cancellation-owner")
    raise RuntimeError("Sensitive fake provider response must not be persisted")
outcome = execute_approved_run(store, run, permit_external_model=True, model="openai:fictional-test", agent_runner=failing_runner)
assert outcome["status"] == "CANCELLED"
assert outcome["failure_code"] == "cancelled_by_operator"
assert outcome["result"] is None
assert verification_calls == []

run = new("normal-completion-2026")
def healthy_runner(p, model, *, task_id):
    return build_synthetic_decision_card(p)
outcome = execute_approved_run(store, run, permit_external_model=True, model="openai:fictional-test", agent_runner=healthy_runner)
assert outcome["status"] == "SUCCEEDED", outcome
assert verification_calls == ["called"]

run = new("cancel-during-receipt-verification-2026")
def cancel_on_verified(p, card):
    verified = original_verify(p, card)
    store.cancel(run, "cancellation-owner")
    return verified
worker.verify_taskrun_receipt = cancel_on_verified
outcome = execute_approved_run(store, run, permit_external_model=True, model="openai:fictional-test", agent_runner=healthy_runner)
assert outcome["status"] == "CANCELLED"
assert store.get(run)["result"] is None
worker.verify_taskrun_receipt = counting_verify

run = new("lease-expired-probe-2026")
_, lease = store.claim(run, lease_seconds=15)
store.assert_active_lease(run, lease)
with sqlite3.connect(store.path) as conn:
    conn.execute("UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?", (time.time() - 1, run))
try:
    store.assert_active_lease(run, lease)
except TaskRunConflict:
    pass
else:
    raise AssertionError("Expired lease was accepted")
store.cancel(run, "cancellation-owner")
try:
    store.assert_active_lease(run, lease)
except TaskRunConflict:
    pass
else:
    raise AssertionError("Cancelled lease was accepted")
print("cooperative_cancel_fake_model_cases=6/6")
"""


def test_worker_observes_cancellation_after_fake_model_and_on_error(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _SOURCE, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2600:] + result.stdout[-800:]
    assert "cooperative_cancel_fake_model_cases=4/4" in result.stdout
