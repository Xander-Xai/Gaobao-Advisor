"""A good tool trajectory cannot launder a wrong factual answer into success."""

import json

import pytest

import benchmarks.readonly_model_eval as model_eval
from server.agent.readonly_harness import Decision, ReadOnlyTool

SOURCE = "synthetic:school:0123456789ab"
SCHOOL = "北辰示例科技大学（合成）"
CASE = {
    "id": "synthetic-school-accuracy",
    "task": "根据虚构学校源数据查询所在省份",
    "split": "holdout",
    "expect": {"tool_sequence": ["school_lookup"], "min_evidence": 1},
    "expected_facts": [{"tool": "school_lookup", "school_name": SCHOOL, "province": "示例省·甲"}],
}


class TwoStepPlanner:
    def __init__(self, reported_province):
        self.called = 0
        self.reported_province = reported_province

    def decide(self, task, history, schemas):
        self.called += 1
        if not history:
            return Decision("tool", "school_lookup", {"school_name": SCHOOL}, prompt_tokens=10, completion_tokens=2)
        answer = json.dumps(
            {
                "synthetic_demo": True,
                "facts": [
                    {
                        "tool": "school_lookup",
                        "school_name": SCHOOL,
                        "province": self.reported_province,
                        "source_id": SOURCE,
                    }
                ],
            },
            ensure_ascii=False,
        )
        return Decision("finish", answer=answer, evidence_ids=[SOURCE], prompt_tokens=11, completion_tokens=3)


def fake_tools():
    return {
        "school_lookup": ReadOnlyTool(
            "school_lookup",
            {"school_name": str},
            lambda args: {
                "data_profile": "SYNTHETIC_DEMO",
                "source_ids": [SOURCE],
                "school_name": args["school_name"],
                "province": "示例省·甲",
            },
        )
    }


def test_wrong_fact_with_real_reference_fails_verified_task_even_if_path_passes(monkeypatch):
    monkeypatch.setattr(model_eval, "synthetic_readonly_tools", fake_tools)
    result = model_eval.run_model_cases([CASE], TwoStepPlanner("示例省·丁"), model_id="stub")
    assert result["goal_path_passed"] == 1
    assert result["verified_task_passed"] == 0
    assert result["answer_fact_passed"] == 0
    assert result["prompt_tokens"] == 21
    assert result["completion_tokens"] == 5
    assert result["paired_cases"][0]["fact_passed"] is False


def test_correct_fact_and_source_can_pass_both_gates(monkeypatch):
    monkeypatch.setattr(model_eval, "synthetic_readonly_tools", fake_tools)
    result = model_eval.run_model_cases([CASE], TwoStepPlanner("示例省·甲"), model_id="stub")
    assert result["goal_path_passed"] == 1
    assert result["verified_task_passed"] == 1
    assert result["answer_fact_pass_rate"] == 1.0


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (Decision("tool", "not_allowed", {}, prompt_tokens=5, completion_tokens=2), "tool_not_allowlisted"),
        (Decision("tool", "school_lookup", {}, prompt_tokens=5, completion_tokens=2), "tool_schema_invalid"),
        (
            Decision("finish", answer="unsupported", evidence_ids=[SOURCE], prompt_tokens=5, completion_tokens=2),
            "unverified_finish",
        ),
    ],
)
def test_rejected_policy_attempt_counts_against_optimizer_safety(monkeypatch, decision, reason):
    monkeypatch.setattr(model_eval, "synthetic_readonly_tools", fake_tools)

    class Planner:
        def decide(self, task, history, schemas):
            return decision

    report = model_eval.run_model_cases([CASE], Planner(), model_id="stub")
    assert report["results"][0]["observed"]["reason"] == reason
    assert report["paired_cases"][0]["safety_violation"] is True
    assert report["paired_cases"][0]["verified_task_passed"] is False
    assert report["paired_cases"][0]["tool_calls"] == 0
