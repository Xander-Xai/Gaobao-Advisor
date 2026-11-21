"""Synthetic Agent trace contracts: real local loop, no paid provider or personal data."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from benchmarks.decision_card_trace_eval import (
    TRACE_CONTRACT,
    check_trace_receipt,
    grade_step_trace,
    trace_failure_category,
)
from benchmarks.model_run_comparison import _validate, compare_model_runs
from benchmarks.synthetic_optimization_review import build_optimization_review
from server.agent.readonly_harness import Decision, ReadOnlyTool, run_readonly_agent
from tests.test_repeated_model_experiment import pairs


def example_execution(prompt_tokens: int | None = 100, completion_tokens: int | None = 20) -> dict:
    tools = ["major_lookup", "admission_lookup", "admission_lookup", "plan_lookup", "plan_lookup"]
    prompt = (
        [prompt_tokens // 6] * 5 + [prompt_tokens - (prompt_tokens // 6) * 5]
        if prompt_tokens is not None
        else [None] * 6
    )
    completion = (
        [completion_tokens // 6] * 5 + [completion_tokens - (completion_tokens // 6) * 5]
        if completion_tokens is not None
        else [None] * 6
    )
    events = [
        {
            "step": i + 1,
            "kind": "tool",
            "tool": tool,
            "result": "ok",
            "observed_source_count": 1,
            "elapsed_ms": 2.5,
            "model_elapsed_ms": 1.5,
            "prompt_tokens": prompt[i],
            "completion_tokens": completion[i],
        }
        for i, tool in enumerate(tools)
    ]
    events.append(
        {
            "step": 6,
            "kind": "finish",
            "result": "completed",
            "model_elapsed_ms": 1.5,
            "prompt_tokens": prompt[5],
            "completion_tokens": completion[5],
        }
    )
    return {
        "trace": events,
        "status": "completed",
        "reason": "evidence_bound_finish",
        "model_steps": 6,
        "tool_calls": 5,
        "tool_sequence": tools,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "os_sandbox": False,
    }


def make_row(execution):
    receipt = grade_step_trace(execution)
    return {
        "trace_receipt": receipt,
        "model_steps": execution["model_steps"],
        "tool_calls": execution["tool_calls"],
        "execution_status": execution["status"],
        "execution_reason": execution["reason"],
        "prompt_tokens": execution["prompt_tokens"],
        "completion_tokens": execution["completion_tokens"],
    }


def test_redacted_trace_reconciles_step_token_totals_and_tool_invocations():
    execution = example_execution()
    receipt = grade_step_trace(execution)
    assert receipt["trace_contract"] == TRACE_CONTRACT
    assert receipt["validated_redacted_trace"] is True
    assert receipt["model_decisions"] == 6
    assert receipt["executed_tool_calls"] == 5
    assert receipt["provider_prompt_tokens"] == 100
    assert receipt["provider_completion_tokens"] == 20
    assert receipt["provider_origin_independently_attested"] is False
    assert receipt["complete_model_internal_reasoning"] is False
    serialized = json.dumps(receipt, ensure_ascii=False)
    for forbidden in ("school_name", "arguments", "observation", "source_ids", "api_key", "prompt_text"):
        assert forbidden not in serialized
    check_trace_receipt(make_row(execution))


@pytest.mark.parametrize(
    "problem",
    [
        "raw_args",
        "raw_output",
        "extra_event",
        "repeated_step",
        "unlisted_tool",
        "wrong_tokens",
        "wrong_calls",
        "wrong_sequence",
        "wrong_status",
        "missing_latency",
        "tool_success_without_evidence_count",
        "malformed_token_usage",
    ],
)
def test_trace_grader_fails_closed_on_tampering_or_privacy_leak(problem):
    execution = example_execution()
    events = execution["trace"]
    if problem == "raw_args":
        events[0]["arguments"] = {"school_name": "must_not_be_exported"}
    elif problem == "raw_output":
        events[0]["raw_model_response"] = "untrusted free form"
    elif problem == "extra_event":
        events.append(dict(events[-1]))
    elif problem == "repeated_step":
        events[3]["step"] = 2
    elif problem == "unlisted_tool":
        events[0]["tool"] = "shell"
    elif problem == "wrong_tokens":
        execution["prompt_tokens"] = 101
    elif problem == "wrong_calls":
        execution["tool_calls"] = 4
    elif problem == "wrong_sequence":
        execution["tool_sequence"][0] = "plan_lookup"
    elif problem == "wrong_status":
        execution["reason"] = "planner_failed"
    elif problem == "missing_latency":
        del events[1]["model_elapsed_ms"]
    elif problem == "tool_success_without_evidence_count":
        del events[1]["observed_source_count"]
    elif problem == "malformed_token_usage":
        events[0]["prompt_tokens"] = True
    with pytest.raises(ValueError):
        grade_step_trace(execution)


def test_receipt_hash_and_row_counters_are_rechecked_by_compare_gate():
    reports = pairs()
    for old, new in reports:
        for report in (old, new):
            report["trace_contract"] = TRACE_CONTRACT
            report["trace_validated_cases"] = 8
            for row in report["results"]:
                trace = example_execution(row["prompt_tokens"], row["completion_tokens"])
                row.update(make_row(trace))
            _validate(report)
    assert compare_model_runs(*reports[0])["holdout_quality_non_regression"] is True
    analysis = build_optimization_review(reports)
    assert analysis["development_trace_covered_cases"] == 16
    assert analysis["development_trace_failures_by_arm"] == {"baseline": {}, "evidence_first": {}}
    assert analysis["model_provenance_verified"] is False
    assert analysis["production_deployment_authorized"] is False
    mutated = copy.deepcopy(reports[0][0])
    mutated["results"][0]["trace_receipt"]["step_events"][0]["prompt_tokens"] += 1
    with pytest.raises(ValueError):
        _validate(mutated)
    mutated = copy.deepcopy(reports[0][0])
    mutated["results"][0]["trace_receipt"]["trace_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        _validate(mutated)
    mutated = copy.deepcopy(reports[0][0])
    mutated["results"][0]["trace_receipt"]["step_events"][0]["raw_prompt"] = "not allowed"
    with pytest.raises(ValueError):
        _validate(mutated)
    mutated = copy.deepcopy(reports[0][0])
    del mutated["results"][0]["trace_receipt"]
    with pytest.raises(ValueError):
        _validate(mutated)


def test_actual_planner_failure_is_sanitized_and_usage_unknown():
    class ExplodingPlanner:
        def decide(self, *args):
            raise RuntimeError("do-not-export-provider-or-user-payload")

    tool = ReadOnlyTool(
        "major_lookup",
        {"major_name": str},
        lambda _args: {"data_profile": "SYNTHETIC_DEMO", "source_ids": []},
    )
    result = run_readonly_agent("fictional", ExplodingPlanner(), {"major_lookup": tool})
    assert result.status == "blocked"
    assert result.reason == "planner_failed"
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    receipt = grade_step_trace(result.public_report())
    assert receipt["model_decisions"] == 1
    assert receipt["step_events"][0]["kind"] == "planner_error"
    assert "do-not-export" not in json.dumps(receipt)
    row = make_row(result.public_report())
    assert trace_failure_category(row) == "planner_failed"


def test_scripted_loop_trace_is_measurement_of_actions_not_model_tokens():
    class OneToolThenStop:
        def decide(self, task, history, tool_schemas):
            if history:
                return Decision("abstain")
            return Decision("tool", tool="major_lookup", arguments={"major_name": "fictional"})

    tool = ReadOnlyTool(
        "major_lookup",
        {"major_name": str},
        lambda _args: {
            "data_profile": "SYNTHETIC_DEMO",
            "source_ids": ["synthetic:major:0123456789ab"],
        },
    )
    result = run_readonly_agent("fictional", OneToolThenStop(), {"major_lookup": tool})
    assert result.status == "abstained"
    assert result.prompt_tokens is None
    receipt = grade_step_trace(result.public_report())
    assert receipt["model_decisions"] == 2
    assert receipt["provider_prompt_tokens"] is None
    assert receipt["step_events"][0]["tool"] == "major_lookup"


def test_baseline_cli_exports_eight_no_key_redacted_trace_receipts(tmp_path):
    target = tmp_path / "fictional-trace.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "benchmarks.decision_card_business_eval", "--out", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=110,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-1300:]
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["trace_contract"] == TRACE_CONTRACT
    assert report["trace_validated_cases"] == 8
    assert report["passed"] == 8
    assert report["controller"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert report["comparison_eligible"] is False
    for row in report["results"]:
        check_trace_receipt(row)
        assert row["trace_receipt"]["model_decisions"] == row["model_steps"]
        assert row["trace_receipt"]["executed_tool_calls"] == row["tool_calls"]
    serialized = target.read_text(encoding="utf-8")
    for forbidden in ("school_name", "source_ids", "api_key", "openai_api_key"):
        assert forbidden not in serialized
    assert report["real_admissions_accuracy"] == "NOT_MEASURED"
