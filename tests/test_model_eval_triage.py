"""No-network synthetic model evaluation triage regression tests."""

import copy

import pytest

from benchmarks.model_eval_triage import summarize_model_failures


def _report():
    rows = []
    for i in range(8):
        rows.append(
            {
                "case_id": f"case-{i}",
                "split": "holdout" if i < 4 else "development",
                "passed": True,
                "safe_abstain": i % 2 == 0,
                "failure_codes": [],
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "tool_calls": 5,
                "elapsed_ms": 50.0,
            }
        )
    return {
        "controller": "REAL_MODEL_NOT_SCRIPTED",
        "evaluation_contract": "SYNTHETIC_DECISION_CARD_V1",
        "dataset_profile": "SYNTHETIC_DEMO",
        "human_review_complete": False,
        "comparison_eligible": True,
        "run_id": "synthetic_eval_test",
        "model_id": "provider-test-model",
        "temperature": 0,
        "dataset_sha256": "a" * 64,
        "model_prompt_sha256": "b" * 64,
        "tool_contract_sha256": "c" * 64,
        "provider_token_source": "OPENAI_COMPATIBLE_REPORTED_USAGE",
        "cases": 8,
        "passed": 8,
        "provider_prompt_tokens": 800,
        "provider_completion_tokens": 160,
        "results": rows,
    }


def test_triage_counts_failure_codes_and_holdout_without_promoting():
    report = _report()
    report["results"][0]["passed"] = False
    report["results"][0]["failure_codes"] = ["wrong_tool", "missing_source", "wrong_tool"]
    report["passed"] = 7
    out = summarize_model_failures(report)
    assert out["by_split"]["holdout"]["cases"] == 4
    assert out["by_split"]["holdout"]["declared_oracle_passes"] == 3
    assert out["by_split"]["holdout"]["failed_case_ids"] == ["case-0"]
    assert out["failure_code_counts"] == {"missing_source": 1, "wrong_tool": 1}
    assert out["by_split"]["development"]["failed_case_ids"] == []
    assert out["production_deployment_authorized"] is False
    assert out["provenance_independently_verified"] is False


def test_triage_rejects_deterministic_baseline_as_real_model():
    report = _report()
    report["controller"] = "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    with pytest.raises(ValueError):
        summarize_model_failures(report)


def test_triage_rejects_fake_or_incomplete_provider_tokens():
    report = _report()
    report["results"][0]["prompt_tokens"] = None
    with pytest.raises(ValueError):
        summarize_model_failures(report)


def test_triage_does_not_modify_input():
    report = _report()
    before = copy.deepcopy(report)
    summarize_model_failures(report)
    assert report == before
