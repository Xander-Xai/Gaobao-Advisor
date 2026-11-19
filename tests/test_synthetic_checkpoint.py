"""Real sqlite process-restart and idempotent replay tests for synthetic runs."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from server.agent.synthetic_checkpoint import CheckpointConflict, SQLiteSyntheticCheckpointStore, task_digest

SOURCE = "synthetic:school:0123456789ab"


def test_checkpoint_version_cas_refuses_overwrite(tmp_path):
    store = SQLiteSyntheticCheckpointStore(tmp_path / "run.db")
    digest = task_digest("fictional")
    state = {"data_profile": "SYNTHETIC_DEMO", "phase": "active", "model_steps": 1}
    assert store.save("synthetic-run-123", digest, None, state) == 0
    assert store.load("synthetic-run-123", digest) == (0, state)
    assert store.save("synthetic-run-123", digest, 0, {**state, "model_steps": 2}) == 1
    with pytest.raises(CheckpointConflict, match="stale"):
        store.save("synthetic-run-123", digest, 0, state)


def test_run_id_reuse_with_different_task_is_rejected(tmp_path):
    store = SQLiteSyntheticCheckpointStore(tmp_path / "run.db")
    store.save("synthetic-run-123", task_digest("synthetic A"), None, {"data_profile": "SYNTHETIC_DEMO"})
    with pytest.raises(ValueError, match="different task"):
        store.load("synthetic-run-123", task_digest("synthetic B"))


def test_non_synthetic_ids_and_snapshots_are_rejected(tmp_path):
    store = SQLiteSyntheticCheckpointStore(tmp_path / "run.db")
    with pytest.raises(ValueError, match="synthetic-run"):
        store.load("../production-run", task_digest("synthetic"))
    with pytest.raises(ValueError, match="synthetic"):
        store.save("synthetic-run-123", task_digest("synthetic"), None, {"data_profile": "REAL_DATA"})


_PROCESS = """
import json
import sys
from pathlib import Path
from server.agent.readonly_harness import Decision, ReadOnlyTool, run_readonly_agent
from server.agent.synthetic_checkpoint import SQLiteSyntheticCheckpointStore

mode, dbpath, counterpath = sys.argv[1:4]
counter = Path(counterpath)
source = "synthetic:school:0123456789ab"

def tool(args):
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(count + 1), encoding="utf-8")
    return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [source], "school_name": args["school_name"]}

class Planner:
    def decide(self, task, history, schemas):
        if mode == "first":
            if not history:
                return Decision("tool", "school_lookup", {"school_name": "北辰示例科技大学（合成）"})
            raise RuntimeError("deliberate simulated provider interruption")
        if mode == "resume":
            assert len(history) == 1
            return Decision("finish", answer="合成院校查询完成", evidence_ids=[source])
        raise AssertionError("A finished run must never ask its planner again")

result = run_readonly_agent(
    "只读合成院校查询",
    Planner(),
    {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, tool)},
    checkpoint=SQLiteSyntheticCheckpointStore(Path(dbpath)),
    run_id="synthetic-run-cross-process-1",
)
print(json.dumps({"reason": result.reason, "status": result.status, "tool_calls": result.tool_calls}))
"""


def test_cross_process_interruption_resume_and_no_duplicate_tools(tmp_path):
    database = str(tmp_path / "run.db")
    counter = tmp_path / "tool-count.txt"
    results = []
    for mode in ("first", "resume", "completed"):
        process = subprocess.run(
            [sys.executable, "-c", _PROCESS, mode, database, str(counter)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert process.returncode == 0, process.stderr
        results.append(json.loads(process.stdout))
    assert [item["reason"] for item in results] == [
        "planner_failed",
        "evidence_bound_finish",
        "evidence_bound_finish",
    ]
    assert all(item["tool_calls"] == 1 for item in results)
    assert counter.read_text(encoding="utf-8") == "1"
