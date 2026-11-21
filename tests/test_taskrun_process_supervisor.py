"""Real OS process-group termination for a synthetic approved TaskRun.

All tests run with fabricated task data and no provider key/network calls.
The child command can only be overridden by Python test monkeypatching;
the public CLI always constructs a fixed, non-shell worker invocation.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent import taskrun_supervisor
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process group signals required")

_FAKE_WORKER = r"""
import subprocess
import sys
import time
from pathlib import Path

from server.agent.taskrun_store import SyntheticTaskRunStore

journal = Path(sys.argv[1])
run_id = sys.argv[2]
ready = Path(sys.argv[3])
orphan = Path(sys.argv[4])
store = SyntheticTaskRunStore(journal)
store.claim(run_id, lease_seconds=20)
subprocess.Popen([
    sys.executable, "-c",
    "import time,sys;from pathlib import Path;time.sleep(0.9);Path(sys.argv[1]).write_text('ORPHAN',encoding='utf-8')",
    str(orphan),
])
ready.write_text("RUNNING", encoding="utf-8")
time.sleep(40)
"""

_EXITED_WORKER = r"""
import subprocess
import sys
from pathlib import Path
from server.agent.taskrun_store import SyntheticTaskRunStore

store = SyntheticTaskRunStore(Path(sys.argv[1]))
store.claim(sys.argv[2], lease_seconds=20)
subprocess.Popen([
    sys.executable, "-c",
    "import time,sys;from pathlib import Path;time.sleep(0.9);Path(sys.argv[1]).write_text('ORPHAN',encoding='utf-8')",
    sys.argv[4],
])
Path(sys.argv[3]).write_text("EXITING", encoding="utf-8")
# The worker exits while its same-process-group child is still alive.
"""


_NORMAL = r"""
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
os.environ["APP_ENV"] = "development"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
seed_database(db_path=root / "admissions.sqlite", reset=True)
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_supervisor import supervise_approved_taskrun

profile = {
    "school_a": DEMO_SCHOOLS[0][0],
    "school_b": DEMO_SCHOOLS[6][0],
    "major": DEMO_MAJORS[0][0],
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
}
store = SyntheticTaskRunStore(root / "journal.sqlite")
task = store.create(
    {"data_profile": "SYNTHETIC_DEMO", "profile": profile},
    "deterministic", "fake-owner", "supervisor-normal-2026",
)
store.approve(task["run_id"], "another-admin")
finished = supervise_approved_taskrun(store, task["run_id"], max_seconds=15)
assert finished["status"] in {"SUCCEEDED", "NEEDS_REVIEW"}, finished
assert finished["supervisor_reason"] == "EXITED", finished
assert finished["local_process_group_termination_sent"] is True  # Signal the owned group even if its leader is a zombie.
assert finished["worker_exit_code"] == 0
assert finished["requires_lease_recovery"] is False
assert finished["remote_provider_cancel_confirmed"] is False
assert store.get(task["run_id"])["attempts"] == 1
assert store.get(task["run_id"])["result"] is not None
print("real_supervised_worker_finished=1/1")
"""


def _approved_task(tmp_path):
    store = SyntheticTaskRunStore(tmp_path / "journal.sqlite")
    payload = {
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
    task = store.create(payload, "deterministic", "synthetic-owner", "supervisor-test-2026")
    store.approve(task["run_id"], "different-admin")
    return store, task["run_id"]


def _enable_synthetic(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _patch_fake_worker(monkeypatch, root):
    ready = root / "claimed.txt"
    orphan = root / "orphaned-grandchild.txt"

    def fake_command(store, run_id, model, permit_external_model):
        assert model is None and permit_external_model is False
        return [sys.executable, "-c", _FAKE_WORKER, str(store.path), run_id, str(ready), str(orphan)]

    monkeypatch.setattr(taskrun_supervisor, "_fixed_worker_command", fake_command)
    return ready, orphan


def _until_ready(path, deadline=4.0):
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError("Worker never claimed its TaskRun")


def test_owner_cancel_revokes_lease_and_supervisor_kills_real_process_tree(tmp_path, monkeypatch):
    _enable_synthetic(monkeypatch)
    store, run_id = _approved_task(tmp_path)
    ready, orphan = _patch_fake_worker(monkeypatch, tmp_path)

    outcome = []
    failure = []

    def run():
        try:
            outcome.append(
                taskrun_supervisor.supervise_approved_taskrun(
                    store, run_id, max_seconds=6, poll_seconds=0.02, term_grace_seconds=0.1
                )
            )
        except Exception as exc:
            failure.append(repr(exc))

    thread = threading.Thread(target=run)
    thread.start()
    _until_ready(ready)
    cancelled = store.cancel(run_id, "synthetic-owner")
    assert cancelled["status"] == "CANCELLED"
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failure, failure
    report = outcome[0]
    assert report["status"] == "CANCELLED"
    assert report["supervisor_reason"] == "CANCELLED"
    assert report["local_process_group_termination_sent"] is True
    assert report["worker_exit_code"] is not None
    assert report["remote_provider_cancel_confirmed"] is False
    assert report["requires_lease_recovery"] is False
    assert store.get(run_id)["result"] is None
    assert store.get(run_id)["attempts"] == 1
    time.sleep(1.05)  # A surviving grandchild would write an orphan marker.
    assert not orphan.exists(), "Process-group cancellation left an orphaned child"


def test_hard_deadline_kills_local_worker_but_keeps_expired_lease_recovery_explicit(tmp_path, monkeypatch):
    _enable_synthetic(monkeypatch)
    store, run_id = _approved_task(tmp_path)
    ready, orphan = _patch_fake_worker(monkeypatch, tmp_path)
    report = taskrun_supervisor.supervise_approved_taskrun(
        store, run_id, max_seconds=0.4, poll_seconds=0.02, term_grace_seconds=0.1
    )
    assert ready.exists()
    assert report["supervisor_reason"] == "DEADLINE_EXCEEDED"
    assert report["local_process_group_termination_sent"] is True
    assert report["requires_lease_recovery"] is True
    assert report["status"] == "RUNNING"  # Do not fabricate CANCELLED or FAILED.
    assert store.get(run_id)["attempts"] == 1
    time.sleep(1.05)
    assert not orphan.exists()


def test_supervisor_kills_orphaned_grandchild_after_worker_exits(tmp_path, monkeypatch):
    _enable_synthetic(monkeypatch)
    store, run_id = _approved_task(tmp_path)
    ready = tmp_path / "worker-exited.txt"
    orphan = tmp_path / "orphan-after-exit.txt"

    def exited_command(store, task_id, model, permit_external_model):
        assert model is None and permit_external_model is False
        return [
            sys.executable,
            "-c",
            _EXITED_WORKER,
            str(store.path),
            task_id,
            str(ready),
            str(orphan),
        ]

    monkeypatch.setattr(taskrun_supervisor, "_fixed_worker_command", exited_command)
    report = taskrun_supervisor.supervise_approved_taskrun(
        store, run_id, max_seconds=6, poll_seconds=0.02, term_grace_seconds=0.1
    )
    assert ready.exists()
    assert report["supervisor_reason"] == "EXITED"
    assert report["local_process_group_termination_sent"] is True
    assert report["worker_exit_code"] == 0
    assert report["status"] == "RUNNING"
    assert report["requires_lease_recovery"] is True
    time.sleep(1.05)
    assert not orphan.exists(), "Supervisor left a grandchild after its worker exited"


def test_real_fixed_worker_subprocess_completes_fictional_sql_case(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", _NORMAL, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2400:] + proc.stdout[-600:]
    assert "real_supervised_worker_finished=1/1" in proc.stdout


def test_supervisor_denies_unapproved_tasks_and_unsafe_environments(tmp_path, monkeypatch):
    _enable_synthetic(monkeypatch)
    store, run_id = _approved_task(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="disabled|unsafe"):
        taskrun_supervisor.supervise_approved_taskrun(store, run_id)
    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(ValueError, match="budget"):
        taskrun_supervisor.supervise_approved_taskrun(store, run_id, max_seconds=float("nan"))
    with pytest.raises(ValueError, match="Deterministic"):
        taskrun_supervisor.supervise_approved_taskrun(
            store, run_id, model="openai:unapproved", permit_external_model=True
        )
    store.cancel(run_id, "synthetic-owner")
    with pytest.raises(TaskRunConflict, match="approved"):
        taskrun_supervisor.supervise_approved_taskrun(store, run_id)
