"""Plan/replan safety contracts and real synthetic-SQL E2E (no paid models)."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.agent.synthetic_replanning import (
    OpenAIReplanningPolicy,
    PlanBudget,
    _validated_plan,
)


def test_plan_budget_rejects_more_than_one_transient_retry():
    with pytest.raises(ValueError, match="transient retry"):
        PlanBudget(max_transient_retries=2)


def test_plan_rejects_unapproved_or_repeated_steps():
    approved = {"tool": "major_lookup", "arguments": {"major_name": "虚构专业（合成）"}}
    remaining = {json.dumps(approved, sort_keys=True, ensure_ascii=False, separators=(",", ":")): approved}
    assert _validated_plan({"kind": "plan", "steps": [approved]}, remaining) == [approved]
    for proposal in (
        {"kind": "plan", "steps": [approved, approved]},
        {"kind": "plan", "steps": [{"tool": "shell", "arguments": {"command": "id"}}]},
        {"kind": "plan", "steps": [{"tool": "major_lookup", "arguments": {"major_name": "真实专业"}}]},
        {"kind": "plan", "steps": []},
        {"kind": "abstain", "prompt": "pretend"},
        {"kind": "finish", "evidence_ids": []},
    ):
        with pytest.raises(ValueError):
            _validated_plan(proposal, remaining)


def test_real_model_adapter_proposes_structured_plan_with_declared_usage():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"abstain"}'))],
            usage=SimpleNamespace(prompt_tokens=31, completion_tokens=5),
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    policy = OpenAIReplanningPolicy(client, "test-model")
    action, usage = policy.propose({"reason": "transient_tool_failure", "remaining_steps": []})
    assert action == {"kind": "abstain"}
    assert usage == (31, 5)
    assert calls[0]["temperature"] == 0
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert json.loads(calls[0]["messages"][1]["content"])["reason"] == "transient_tool_failure"


def test_scripted_replan_with_transient_failure_and_independent_sql_oracle(tmp_path):
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.synthetic_replanning_eval",
        "--inject-transient",
        "--out",
        str(output),
    ]
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert proc.returncode == 0, proc.stdout[-1000:] + proc.stderr[-2500:]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["controller"] == "SCRIPTED_NOT_MODEL"
    assert report["passed"] == report["cases"] == 8
    assert report["replanned_cases"] >= 2
    assert report["replanned_passed"] >= 2
    assert report["human_expert_ground_truth"] is False
    assert report["real_admissions_quality"] == "NOT_MEASURED"
    assert all(r["tool_attempts"] <= 7 and r["plan_rounds"] <= 3 for r in report["results"])
    assert any(
        x["kind"] == "tool" and x["result"] == "transient_failure" for r in report["results"] for x in r["trace"]
    )
    assert all("password" not in str(r).lower() for r in report["results"])


def test_model_like_replanner_reschedules_after_failure_but_is_not_real_llm(tmp_path):
    script = r"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

path = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(path)
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
from scripts.seed_demo_data import seed_database
seed_database(db_path=path, reset=True)
from benchmarks.decision_card_business_eval import frozen_cases
from benchmarks.synthetic_replanning_eval import run_cases
from server.agent.synthetic_replanning import OpenAIReplanningPolicy

class FakeChat:
    def __init__(self):
        self.requests = []
    def create(self, **kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        self.requests.append(payload)
        steps = list(payload["remaining_steps"])
        if payload["reason"] == "transient_tool_failure":
            steps = list(reversed(steps))
        output = {"kind": "plan", "steps": steps}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
        )

fake = FakeChat()
client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
report = run_cases(
    frozen_cases()[:4],
    lambda: OpenAIReplanningPolicy(client, "fake-model-not-provider"),
    inject_transient=True,
    model_mode=True,
)
assert report["passed"] == 4, report
assert report["replanned_cases"] >= 2, report
assert report["controller"] == "OPT_IN_REAL_MODEL"  # Declared mode; fake supplied by this TEST.
assert any(x["reason"] == "transient_tool_failure" for x in fake.requests)
assert all(r["provider_prompt_tokens"] is not None for r in report["results"])
print("FAKE_MODEL_REPLANNING_SQL_ORACLE_PASS")
"""
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "fixture.db")],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-2400:]
    assert "FAKE_MODEL_REPLANNING_SQL_ORACLE_PASS" in proc.stdout


def test_model_cli_refuses_missing_permission_without_network(tmp_path):
    command = [
        sys.executable,
        "-m",
        "benchmarks.synthetic_replanning_eval",
        "--mode",
        "model",
        "--model",
        "test-model",
        "--out",
        str(tmp_path / "report.json"),
    ]
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15, check=False)
    assert proc.returncode != 0
    assert not (tmp_path / "report.json").exists()
    assert "requires explicit permit" in proc.stderr
