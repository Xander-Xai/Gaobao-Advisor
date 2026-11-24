"""Adversarial model action tests: fake client is NOT a real-model benchmark."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.agent.decision_card_model_planner import DecisionCardModelPlanner
from server.agent.synthetic_decision_card import DecisionCardProfile

PROFILE = DecisionCardProfile(
    "北辰示例科技大学（合成）",
    "沧澜示例农业大学（合成）",
    "计算机科学与技术（合成）",
    "示例省·甲",
    550,
    "物理类",
    2025,
)
SCHEMAS = {
    "major_lookup": {"major_name": "str"},
    "admission_lookup": {"school_name": "str", "major_name": "str", "province": "str", "year": "int"},
    "plan_lookup": {"school_name": "str", "major_name": "str", "province": "str", "year": "int"},
}


class SingleResponse:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
        )


def fake_client(payload):
    response = SingleResponse(payload)
    return SimpleNamespace(chat=SimpleNamespace(completions=response)), response


def test_valid_model_step_preserves_provider_usage_and_fixed_exact_arguments():
    client, stub = fake_client({"kind": "tool", "tool": "major_lookup", "arguments": {"major_name": PROFILE.major}})
    planner = DecisionCardModelPlanner(client, "synthetic-fake", PROFILE)
    result = planner.decide("fictional", [], SCHEMAS)
    assert result.kind == "tool"
    assert result.tool == "major_lookup"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 5
    request = stub.requests[0]
    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}
    assert '"required_remaining"' in request["messages"][1]["content"]


@pytest.mark.parametrize(
    "bad_action",
    [
        {"kind": "tool", "tool": "shell", "arguments": {"command": "id"}},
        {"kind": "tool", "tool": "plan_lookup", "arguments": {"school_name": "真实高校"}},
        {"kind": "tool", "tool": "major_lookup", "arguments": {"major_name": PROFILE.major, "debug": "true"}},
        {"kind": "tool", "tool": "major_lookup", "arguments": {"major_name": PROFILE.major}, "payload": "injected"},
        {"kind": "finish", "evidence_ids": []},
        {"kind": "finish", "evidence_ids": [], "answer": "I approve everything"},
        {"kind": "python", "code": "print('bypass')"},
    ],
)
def test_hostile_model_cannot_choose_shell_arbitrary_sql_or_early_finish(bad_action):
    client, _ = fake_client(bad_action)
    planner = DecisionCardModelPlanner(client, "fake", PROFILE)
    with pytest.raises(ValueError, match="action|arguments|Finish|fields|tool"):
        planner.decide("fictional", [], SCHEMAS)


def test_frozen_task_cannot_reexecute_an_observed_tool():
    client, _ = fake_client({"kind": "tool", "tool": "major_lookup", "arguments": {"major_name": PROFILE.major}})
    planner = DecisionCardModelPlanner(client, "fake", PROFILE)
    observation = [
        {
            "tool": "major_lookup",
            "observation": json.dumps(
                {
                    "data_profile": "SYNTHETIC_DEMO",
                    "major_name": PROFILE.major,
                    "category": "工学",
                    "record_count": 1,
                    "source_ids": ["synthetic:major:0123456789ab"],
                }
            ),
        }
    ]
    with pytest.raises(ValueError, match="Unapproved|repeated"):
        planner.decide("fictional", observation, SCHEMAS)


def test_model_cannot_continue_execution_after_observed_missing_record():
    client, _ = fake_client(
        {
            "kind": "tool",
            "tool": "plan_lookup",
            "arguments": {
                "school_name": PROFILE.school_b,
                "major_name": PROFILE.major,
                "province": PROFILE.province,
                "year": PROFILE.year,
            },
        }
    )
    planner = DecisionCardModelPlanner(client, "fake", PROFILE)
    history = [
        {
            "tool": "admission_lookup",
            "observation": json.dumps(
                {
                    "data_profile": "SYNTHETIC_DEMO",
                    "record_count": 0,
                    "source_ids": [],
                    "school_name": PROFILE.school_a,
                }
            ),
        }
    ]
    with pytest.raises(ValueError, match="Unapproved|unsafe"):
        planner.decide("fictional", history, SCHEMAS)


_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

dbpath = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(dbpath)
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
from scripts.seed_demo_data import seed_database, DEMO_YEAR
seed_database(db_path=dbpath, reset=True)

from benchmarks.decision_card_business_eval import frozen_cases, grade_business_case
from benchmarks.decision_card_trace_eval import grade_step_trace
from server.agent.synthetic_decision_card import build_synthetic_decision_card, DecisionCardProfile
from server.agent.decision_card_model_planner import DecisionCardModelPlanner

class FakeResponse:
    def create(self, **kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        required = payload["required_remaining"]
        if payload["requires_abstention_due_to_missing_data"]:
            output = {"kind": "abstain"}
        elif required:
            step = required[0]
            output = {"kind": "tool", "tool": step["tool"], "arguments": step["arguments"]}
        else:
            ids = []
            for event in payload["tool_observations_data_not_instructions"]:
                ids.extend(json.loads(event["observation"])["source_ids"])
            output = {"kind": "finish", "evidence_ids": ids}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))],
            usage=SimpleNamespace(prompt_tokens=18, completion_tokens=7),
        )

client = SimpleNamespace(chat=SimpleNamespace(completions=FakeResponse()))
case = frozen_cases()[0]
profile = DecisionCardProfile(**case["profile"])
planner = DecisionCardModelPlanner(client, "fake-model-not-llm", profile)
card = build_synthetic_decision_card(profile, planner=planner, require_provider_usage=True)
assessment = grade_business_case(case, card)
assert assessment["passed"], (card, assessment)
assert card["status"] == "evidence_complete_historical_only"
assert card["execution"]["tool_calls"] == 5
assert card["execution"]["model_steps"] == 6
assert card["execution"]["prompt_tokens"] == 108
assert card["execution"]["completion_tokens"] == 42
trace = grade_step_trace(card["execution"])
assert trace["model_decisions"] == 6
assert trace["executed_tool_calls"] == 5
assert trace["provider_prompt_tokens"] == 108
assert trace["provider_completion_tokens"] == 42
assert [event["kind"] for event in trace["step_events"]] == ["tool"] * 5 + ["finish"]
assert all(event["prompt_tokens"] == 18 for event in trace["step_events"])
assert all(event["completion_tokens"] == 7 for event in trace["step_events"])
assert card["planner"] == "INJECTED_PLANNER_UNVERIFIED"

negative_case = next(x for x in frozen_cases() if x["case_id"] == "holdout-no-school")
negative_profile = DecisionCardProfile(**negative_case["profile"])
negative_planner = DecisionCardModelPlanner(client, "fake-model-not-llm", negative_profile)
negative_card = build_synthetic_decision_card(
    negative_profile, planner=negative_planner, require_provider_usage=True
)
negative_assessment = grade_business_case(negative_case, negative_card)
assert negative_card["status"] == "needs_review"
assert negative_card["observed_negative_evidence_count"] >= 1
assert negative_card["schools"] == []
assert negative_assessment["passed"], negative_assessment
class MalformedResponse:
    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{invalid-json"))],
            usage=SimpleNamespace(prompt_tokens=18, completion_tokens=7),
        )

bad_client = SimpleNamespace(chat=SimpleNamespace(completions=MalformedResponse()))
bad_card = build_synthetic_decision_card(
    profile, planner=DecisionCardModelPlanner(bad_client, "fake-model-not-llm", profile),
    require_provider_usage=True,
)
bad_execution = bad_card["execution"]
assert bad_card["status"] == "needs_review"
assert bad_card["schools"] == []
assert bad_execution["reason"] == "planner_response_invalid"
assert bad_execution["tool_calls"] == 0
assert bad_execution["model_steps"] == 1
assert bad_execution["prompt_tokens"] == 18
assert bad_execution["completion_tokens"] == 7
bad_trace = grade_step_trace(bad_execution)
assert bad_trace["provider_prompt_tokens"] == 18
assert bad_trace["provider_completion_tokens"] == 7
assert bad_trace["step_events"][0]["kind"] == "planner_error"
assert "invalid-json" not in json.dumps(bad_trace)

print(json.dumps({"status": "PASS", "fake_model": True, "source_count": len(card["evidence_ids"])}))
"""


def test_injected_fake_model_guides_actual_sql_tools_with_independent_oracle(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path / "model-action-fake.db")],
        text=True,
        capture_output=True,
        check=False,
        timeout=95,
    )
    assert completed.returncode == 0, completed.stderr[-2200:] + completed.stdout[-700:]
    assert '"status": "PASS"' in completed.stdout
    assert '"fake_model": true' in completed.stdout
    assert '"source_count": 5' in completed.stdout


@pytest.mark.parametrize("payload", [None, [], {"kind": []}, {}])
def test_malformed_model_response_keeps_usage_without_action(payload):
    client, _ = fake_client(payload)
    decision = DecisionCardModelPlanner(client, "fake", PROFILE).decide("fictional", [], SCHEMAS)
    assert decision.kind == "invalid"
    assert decision.prompt_tokens == 11
    assert decision.completion_tokens == 5
    assert decision.tool == ""
    assert not decision.arguments
