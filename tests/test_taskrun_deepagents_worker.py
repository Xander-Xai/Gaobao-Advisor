"""An approved Deep Agents TaskRun is executable only by an explicitly opted-in worker.

Fake runner tests task orchestration and durable SQL receipt, not real LLM
autonomy. The actual SDK constructor has a separate installation test.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SOURCE = r"""
import json
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(home / "fictional.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
from scripts.seed_demo_data import seed_database, DEMO_YEAR
seed_database(db_path=home / "fictional.sqlite", reset=True)
from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict
from server.agent.taskrun_worker import execute_approved_run

profile = {
  "school_a": "北辰示例科技大学（合成）",
  "school_b": "沧澜示例农业大学（合成）",
  "major": "计算机科学与技术（合成）",
  "province": "示例省·甲",
  "score": 550,
  "subject_type": "物理类",
  "year": DEMO_YEAR,
}
store = SyntheticTaskRunStore(home / "tasks.sqlite")
created = store.create(
    {"data_profile":"SYNTHETIC_DEMO","profile":profile},
    "deepagents", "demo-maker", "test-opt-in-worker-2026",
)
run_id = created["run_id"]
assert created["status"] == "PENDING_APPROVAL"
os.environ["OPENAI_API_KEY"] = "fictional-test-token-" + "x" * 26
calls = []
def synthetic_runner(received_profile, model, task_id):
    calls.append((model, task_id))
    assert model == "openai:fake-planning-runtime"
    assert task_id == run_id
    return build_synthetic_decision_card(received_profile)

try:
    execute_approved_run(
        store, run_id, permit_external_model=True,
        model="openai:fake-planning-runtime", agent_runner=synthetic_runner
    )
except TaskRunConflict:
    pass
else:
    raise AssertionError("Worker allowed an unapproved task")
assert calls == []
store.approve(run_id, "demo-checker")
os.environ.pop("OPENAI_API_KEY")
try:
    execute_approved_run(
        store, run_id, permit_external_model=True,
        model="openai:fake-planning-runtime", agent_runner=synthetic_runner
    )
except ValueError:
    pass
else:
    raise AssertionError("Worker did not enforce missing provider permission")
assert calls == []
os.environ["OPENAI_API_KEY"] = "fictional-test-token-" + "x" * 26
result = execute_approved_run(
    store, run_id, permit_external_model=True,
    model="openai:fake-planning-runtime", agent_runner=synthetic_runner
)
assert result["status"] == "SUCCEEDED", result
assert result["engine"] == "deepagents"
assert result["attempts"] == 1
assert len(result["result"]["evidence_ids"]) == 5
assert len(calls) == 1
assert store.get(run_id)["status"] == "SUCCEEDED"
try:
    execute_approved_run(
        store, run_id, permit_external_model=True,
        model="openai:fake-planning-runtime", agent_runner=synthetic_runner
    )
except TaskRunConflict:
    pass
else:
    raise AssertionError("Completed TaskRun was reexecuted")
assert len(calls) == 1
print(json.dumps({"status":"PASS","controller":"FAKE_NOT_REAL_MODEL","durable_taskrun":"SUCCEEDED","call_count":len(calls)}))
"""


def test_independent_deepagents_worker_requires_approval_and_model_permission(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    process = subprocess.run(
        [sys.executable, "-c", _SOURCE, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert process.returncode == 0, process.stderr[-2500:] + process.stdout[-700:]
    assert '"status": "PASS"' in process.stdout
    assert '"call_count": 1' in process.stdout
