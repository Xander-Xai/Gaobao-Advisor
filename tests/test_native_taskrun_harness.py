"""Actual approved TaskRun + native loop + seeded SQL; stub provider, not model quality."""

import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

home = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(home / "fictional.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["OPENAI_API_KEY"] = "fictional-test-key"
from scripts.seed_demo_data import seed_database, DEMO_YEAR
seed_database(db_path=home / "fictional.sqlite", reset=True)
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict
from server.agent.taskrun_worker import execute_approved_run

profile = {
    "school_a": "北辰示例科技大学（合成）",
    "school_b": "沧澜示例农业大学（合成）",
    "major": "计算机科学与技术（合成）",
    "province": "示例省·甲", "score": 550, "subject_type": "物理类", "year": DEMO_YEAR,
}
store = SyntheticTaskRunStore(home / "tasks.sqlite")
calls = []
mode = "normal"

class Stub:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        assert kwargs["max_retries"] == 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def create(self, **kwargs):
        calls.append(kwargs)
        request = json.loads(kwargs["messages"][1]["content"])
        if request["requires_abstention_due_to_missing_data"]:
            output = {"kind": "abstain"}
        elif request["required_remaining"]:
            step = request["required_remaining"][0]
            output = {"kind": "tool", **step}
        else:
            refs = [ref for row in request["tool_observations_data_not_instructions"]
                    for ref in json.loads(row["observation"])["source_ids"]]
            output = {"kind": "finish", "evidence_ids": refs}
        if mode == "cancel":
            store.cancel(run_id, "maker")
        return SimpleNamespace(
            model="version-drift" if mode == "drift" and len(calls) > 1 else "fixed-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))],
            usage=SimpleNamespace(prompt_tokens=18, completion_tokens=7),
        )

def create(key, payload=profile):
    return store.create({"data_profile": "SYNTHETIC_DEMO", "profile": payload},
                        "native_harness", "maker", key)["run_id"]

def execute(run):
    return execute_approved_run(store, run, permit_external_model=True, model="openai:stub-model")

with patch("openai.OpenAI", Stub):
    run_id = create("native-test-positive")
    try:
        execute(run_id)
    except TaskRunConflict:
        pass
    else:
        raise AssertionError("unapproved run executed")
    assert not calls
    store.approve(run_id, "checker")
    try:
        execute_approved_run(store, run_id)
    except ValueError:
        pass
    else:
        raise AssertionError("missing authorization accepted")
    assert not calls
    result = execute(run_id)
    assert result["status"] == "SUCCEEDED", result
    assert len(calls) == 6
    receipt = store.get(run_id)["result"]
    assert receipt["planner"] == "NATIVE_HARNESS_PROVIDER_DECLARED"
    assert len(receipt["evidence_ids"]) == 5
    assert receipt["execution"]["prompt_tokens"] == 108
    assert receipt["execution"]["provider_model_identity"]["reported"] == "fixed-model"

    calls.clear()
    run_id = create("native-test-negative", {**profile, "school_b": "海岳示例师范大学（合成）"})
    store.approve(run_id, "checker")
    result = execute(run_id)
    assert result["status"] == "NEEDS_REVIEW", result
    assert result["result"]["schools"] == []
    assert result["result"]["observed_negative_evidence_count"] >= 1

    calls.clear()
    mode = "drift"
    run_id = create("native-test-drift")
    store.approve(run_id, "checker")
    result = execute(run_id)
    assert result["status"] == "FAILED" and result["result"] is None
    assert len(calls) == 2

    calls.clear()
    mode = "cancel"
    run_id = create("native-test-cancel")
    store.approve(run_id, "checker")
    result = execute(run_id)
    assert result["status"] == "CANCELLED" and result["result"] is None
    assert len(calls) == 1

print("native_taskrun_stub_sql_verified")
"""


def test_approved_native_harness_sql_and_negative_controls(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-1000:]
    assert "native_taskrun_stub_sql_verified" in result.stdout
