"""Regression: never trust Agent-returned business status, source IDs or fields.

A fresh subprocess creates an isolated fictional SQLite dataset. Every runner
below is a fake, not a real LLM evaluation or a Deep Agents benchmark.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SCRIPT = r"""
import os
import sys
from pathlib import Path

home = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(home / "fictional.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["OPENAI_API_KEY"] = "fake-test-credential-" + "x" * 24

from scripts.seed_demo_data import DEMO_SCHOOLS, DEMO_MAJORS, DEMO_YEAR, seed_database
from server.agent.synthetic_decision_card import build_synthetic_decision_card
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_worker import execute_approved_run

seed_database(db_path=home / "fictional.sqlite", reset=True)
store = SyntheticTaskRunStore(home / "task-journal.sqlite")
profile = {
    "school_a": DEMO_SCHOOLS[0][0],
    "school_b": DEMO_SCHOOLS[6][0],
    "major": DEMO_MAJORS[0][0],
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
}

def run(case, fn, *, params=None):
    p = dict(profile)
    if params:
        p.update(params)
    created = store.create(
        {"data_profile": "SYNTHETIC_DEMO", "profile": p},
        "deepagents", "test-operator", "receipt-" + case + "-2026",
    )
    store.approve(created["run_id"], "second-admin")
    def runner(received, model, task_id):
        assert task_id == created["run_id"]
        return fn(received)
    return execute_approved_run(
        store, created["run_id"], permit_external_model=True,
        model="openai:test-fixture", agent_runner=runner,
    )

valid = run("valid", lambda p: build_synthetic_decision_card(p))
assert valid["status"] == "SUCCEEDED", valid
assert len(valid["result"]["evidence_ids"]) == 5

# The earlier worker checked only these two strings. It would have accepted
# this fabricated "success" despite missing all five SQL sources.
forged = run(
    "forged", lambda p: {
        "data_profile": "SYNTHETIC_DEMO",
        "status": "evidence_complete_historical_only",
        "schools": [{"school_name": "invented"}],
    }
)
assert forged["status"] == "FAILED" and forged["result"] is None, forged
assert forged["failure_code"] == "agent_execution_failed"

def tamper_score(p):
    card = build_synthetic_decision_card(p)
    card["schools"][0]["historical_min_score"] += 1
    return card

changed = run("tampered-sql", tamper_score)
assert changed["status"] == "FAILED" and changed["result"] is None, changed

def tamper_profile(p):
    card = build_synthetic_decision_card(p)
    card["profile"]["score"] += 2
    return card

wrong = run("tampered-profile", tamper_profile)
assert wrong["status"] == "FAILED" and wrong["result"] is None, wrong

def pretend_abstain(p):
    card = build_synthetic_decision_card(p)
    card["status"] = "needs_review"
    card["reason"] = "explicit_abstention"
    card["schools"] = []
    card["evidence_ids"] = []
    card["missing_evidence"] = ["fabricated missing item"]
    card["observed_negative_evidence_count"] = 1
    return card

blind = run("fake-abstention", pretend_abstain)
assert blind["status"] == "FAILED", blind

# A genuinely missing source must still be allowed as an explicitly supported
# negative research result, never converted into false historical advice.
missing = run(
    "real-negative",
    lambda p: build_synthetic_decision_card(p),
    params={"province": "示例省·丙"},
)
assert missing["status"] == "NEEDS_REVIEW", missing
assert missing["result"]["schools"] == []
assert missing["result"]["evidence_ids"] == []
print("taskrun-receipt-independent-sql-regression: PASS")
"""


def test_taskrun_receipt_is_rechecked_against_real_synthetic_sql(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    process = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert process.returncode == 0, process.stderr[-2500:] + process.stdout[-1200:]
    assert "taskrun-receipt-independent-sql-regression: PASS" in process.stdout
