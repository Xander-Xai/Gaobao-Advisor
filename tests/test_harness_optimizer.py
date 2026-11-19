"""Optimization must not confuse scripted metrics or prompt tweaks with wins."""

import copy

import pytest

from benchmarks.harness_optimizer import compare_holdout, propose_bounded_changes


def paired_fixture():
    baseline = {
        "controller": "REAL_MODEL_NOT_SCRIPTED",
        "dataset_sha256": "a" * 64,
        "model_id": "explicit-demo-model",
        "groundtruth_review": "REVIEW_DECLARED",
        "paired_cases": [
            {
                "case_id": f"holdout-{i}",
                "split": "holdout",
                "fact_passed": True,
                "tool_calls": 4,
                "total_tokens": 100,
                "safety_violation": False,
            }
            for i in range(5)
        ],
    }
    candidate = copy.deepcopy(baseline)
    for row in candidate["paired_cases"]:
        row["tool_calls"] = 3
        row["total_tokens"] = 90
    return baseline, candidate


def test_candidate_is_proposed_for_review_not_deployed():
    base, candidate = paired_fixture()
    report = compare_holdout(base, candidate)
    assert report["decision"] == "REVIEW_REQUIRED"
    assert report["production_deployment_authorized"] is False
    assert report["token_delta"] == -50
    assert report["tool_call_delta"] == -5
    assert report["reproducibility"] == "NOT_ESTABLISHED_BY_SINGLE_COMPARISON"


def test_quality_regression_blocks_promotion_despite_token_savings():
    base, candidate = paired_fixture()
    candidate["paired_cases"][0]["fact_passed"] = False
    report = compare_holdout(base, candidate)
    assert report["decision"] == "REJECT_CANDIDATE"


def test_new_safety_violation_blocks_promotion():
    base, candidate = paired_fixture()
    candidate["paired_cases"][0]["safety_violation"] = True
    assert compare_holdout(base, candidate)["decision"] == "REJECT_CANDIDATE"


def test_scripted_evaluation_cannot_pass_model_improvement_gate():
    base, candidate = paired_fixture()
    candidate["controller"] = "SCRIPTED_NOT_LLM"
    with pytest.raises(ValueError, match="real-model"):
        compare_holdout(base, candidate)


def test_unreviewed_dataset_cannot_pass_improvement_gate():
    base, candidate = paired_fixture()
    candidate["groundtruth_review"] = "PENDING_HUMAN_REVIEW"
    with pytest.raises(ValueError, match="review"):
        compare_holdout(base, candidate)


def test_missing_provider_usage_or_split_change_fails_closed():
    base, candidate = paired_fixture()
    candidate["paired_cases"][0]["total_tokens"] = None
    with pytest.raises(ValueError, match="provider usage"):
        compare_holdout(base, candidate)
    base, candidate = paired_fixture()
    candidate["paired_cases"][0]["split"] = "development"
    with pytest.raises(ValueError, match="split changed"):
        compare_holdout(base, candidate)


def test_bounded_suggestions_are_explicitly_untested():
    proposals = propose_bounded_changes(["tool_repeat_loop", "fact_mismatch", "reported_token_budget_exhausted"])
    assert len(proposals) == 3
    assert all(p["state"] == "UNTESTED" for p in proposals)
    assert all(isinstance(p["configuration"], dict) for p in proposals)
