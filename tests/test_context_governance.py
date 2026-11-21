"""Regression checks for JSON context budgets and trace accounting."""

import json

import pytest

from benchmarks.decision_card_trace_eval import grade_step_trace
from server.agent.context_policy import ContextBudgetExceeded, bound_history, encode_observation
from server.agent.readonly_harness import Decision, HarnessLimits, ReadOnlyTool, run_readonly_agent

SOURCE = "synthetic:school:0123456789ab"


class Planner:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.seen = []

    def decide(self, task, history, schemas):
        self.seen.append(history)
        return next(self.actions)


def large_tool(args):
    return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [SOURCE], "content": "large " * 300}


def test_observation_always_valid_json_or_fails_closed():
    packed = encode_observation(large_tool({}), 180)
    result = json.loads(packed)
    assert len(packed) <= 180
    assert result["source_ids"] == [SOURCE]
    assert result["observation_truncated"] is True
    assert "content" not in result
    with pytest.raises(ContextBudgetExceeded):
        encode_observation(large_tool({}), 20)


def test_single_history_record_repacked_without_broken_json():
    observation = encode_observation(large_tool({}), 99999)
    bounded, evicted = bound_history(
        [{"tool": "school_lookup", "observation": observation, "source_ids": [SOURCE]}], 250
    )
    assert evicted == 0
    assert len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) <= 250
    assert json.loads(bounded[0]["observation"])["observation_truncated"] is True
    with pytest.raises(ContextBudgetExceeded):
        bound_history(bounded, 20)


def test_compaction_rejects_non_list_source_ids():
    malformed = [{"tool": "school_lookup", "observation": '{"source_ids":"abc"}', "source_ids": "abc"}]
    with pytest.raises(ContextBudgetExceeded, match="source IDs"):
        bound_history(malformed, 40)

def test_tiny_context_budget_is_terminal_and_trace_reconciles():
    planner = Planner([Decision("tool", "school_lookup", {"school_name": "A"}, prompt_tokens=4, completion_tokens=2)])
    result = run_readonly_agent(
        "synthetic",
        planner,
        {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, large_tool)},
        HarnessLimits(max_context_chars=20, max_observation_chars=180),
    )
    assert result.status == "blocked"
    assert result.reason == "context_budget_exhausted"
    assert result.tool_calls == 1
    # The decision-card trace contract has a narrower allowlist, but school
    # tool metadata is intentionally synthetic and must not be counterfeited.
    assert result.trace[-1]["result"] == "context_budget_exhausted"
    assert result.tool_sequence == ["school_lookup"]


def test_context_cap_retains_source_and_safe_json():
    planner = Planner(
        [
            Decision("tool", "school_lookup", {"school_name": "A"}),
            Decision("tool", "school_lookup", {"school_name": "B"}),
            Decision("finish", answer="Synthetic only", evidence_ids=[SOURCE]),
        ]
    )
    result = run_readonly_agent(
        "synthetic",
        planner,
        {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, large_tool)},
        HarnessLimits(max_context_chars=250, max_observation_chars=180),
    )
    assert result.status == "completed"
    assert result.max_history_chars <= 250
    assert result.history_evictions >= 1
    for item in planner.seen[-1]:
        assert json.loads(item["observation"])["source_ids"] == [SOURCE]


def test_trace_grader_counts_executed_context_failures():
    execution = {
        "os_sandbox": False,
        "trace": [
            {
                "step": 1,
                "kind": "tool",
                "tool": "major_lookup",
                "result": "context_budget_exhausted",
                "model_elapsed_ms": 1.0,
                "prompt_tokens": 4,
                "completion_tokens": 2,
            }
        ],
        "model_steps": 1,
        "tool_calls": 1,
        "tool_sequence": ["major_lookup"],
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "status": "blocked",
        "reason": "context_budget_exhausted",
    }
    assert grade_step_trace(execution)["executed_tool_calls"] == 1
