"""Frozen business case coverage audit must not make model or real-data claims."""

import copy

import pytest

from benchmarks.business_eval_coverage import evaluate_case_coverage
from benchmarks.decision_card_business_eval import frozen_cases


def test_frozen_suite_exposes_held_out_and_negative_case_balance():
    report = evaluate_case_coverage(frozen_cases())
    assert report["sample_count"] == 8
    assert report["split_status_counts"]["holdout"] == {
        "evidence_complete_historical_only": 2,
        "needs_review": 2,
    }
    assert report["split_status_counts"]["development"] == {
        "evidence_complete_historical_only": 2,
        "needs_review": 2,
    }
    assert report["distinct_dimension_counts"]["major"] == 1
    assert report["distinct_dimension_counts"]["subject_type"] == 1
    assert "small_synthetic_sample_count" in report["coverage_gaps"]
    assert "no_real_admissions_ground_truth" in report["coverage_gaps"]
    assert report["model_task_success"] == "NOT_MEASURED"
    assert report["production_deployment_authorized"] is False


def test_missing_holdout_is_explicit_gap_not_silent_pass():
    cases = [case for case in frozen_cases() if case["split"] == "development"]
    report = evaluate_case_coverage(cases)
    assert report["sample_count"] == 4
    assert "missing_holdout_split" in report["coverage_gaps"]


def test_duplicate_case_identity_is_rejected():
    cases = frozen_cases()
    cases.append(copy.deepcopy(cases[0]))
    with pytest.raises(ValueError, match="duplicate case_id"):
        evaluate_case_coverage(cases)


def test_non_synthetic_or_malformed_cases_are_rejected():
    cases = frozen_cases()
    cases[0]["data_profile"] = "REAL_STUDENT"
    with pytest.raises(ValueError, match="synthetic"):
        evaluate_case_coverage(cases)
    cases = frozen_cases()
    del cases[0]["profile"]["major"]
    with pytest.raises(ValueError, match="Missing major"):
        evaluate_case_coverage(cases)
