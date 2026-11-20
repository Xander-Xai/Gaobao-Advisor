"""Synthetic task metrics must never be mistaken for real business GT or bills."""

from __future__ import annotations

import copy

import pytest

from benchmarks.agent_delivery_scorecard import summarize_synthetic_delivery
from tests.test_model_run_comparison import fixture


def complete_run():
    report = fixture("scorecard-case")
    for item in report["results"]:
        item["model_steps"] = 6
    report["total_tool_calls"] = 40
    return report


def test_redacted_scorecard_calculates_synthetic_metrics_without_pretending_real_gt():
    output = summarize_synthetic_delivery(complete_run())
    assert output["synthetic_case_count"] == 8
    assert output["synthetic_verified_case_count"] == 8
    assert output["synthetic_task_pass_rate"] == 1.0
    assert output["p95_synthetic_elapsed_ms"] == 100.0
    assert output["average_tool_calls_per_passing_case"] == 5.0
    assert output["total_provider_declared_tokens"] == 960
    assert output["provider_declared_tokens_per_passing_case"] == 120.0
    assert output["tool_contract_success_rate"] is None
    assert output["real_user_effective_delivery_rate"] == "NOT_MEASURED"
    assert output["provider_billing_verified"] is False
    assert output["production_release_authorized"] is False
    assert "test-provider-model" not in str(output)


@pytest.mark.parametrize("tamper", ["duplicate", "raw_trace", "wrong_count", "wrong_source", "bad_tokens", "inf_latency"])
def test_invalid_claims_fail_closed(tamper):
    report = complete_run()
    if tamper == "duplicate":
        report["results"][1]["case_id"] = report["results"][0]["case_id"]
    elif tamper == "raw_trace":
        report["results"][0]["trace_receipt"] = {"leak": "private"}
    elif tamper == "wrong_count":
        report["passed"] = 7
    elif tamper == "wrong_source":
        report["dataset_profile"] = "REAL_UNLICENSED_DATA"
    elif tamper == "bad_tokens":
        report["provider_completion_tokens"] = 159
    elif tamper == "inf_latency":
        report["results"][0]["elapsed_ms"] = float("inf")
    with pytest.raises(ValueError):
        summarize_synthetic_delivery(report)


def test_zero_success_does_not_forge_per_success_metric():
    report = complete_run()
    no_success = copy.deepcopy(report)
    for case in no_success["results"]:
        case["passed"] = False
    no_success["passed"] = 0
    result = summarize_synthetic_delivery(no_success)
    assert result["average_tool_calls_per_passing_case"] is None
    assert result["provider_declared_tokens_per_passing_case"] is None
