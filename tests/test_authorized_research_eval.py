"""Gold labels are test fixtures, not real-world admissions evidence."""

from copy import deepcopy

import pytest

from benchmarks.authorized_research_eval import SCHEMA, digest, evaluate


def cohort(index):
    return {
        "school_ids": [101 + index * 2, 102 + index * 2],
        "major_id": 42,
        "province": "广东",
        "subject_type": "物理",
        "batch": "本科",
        "year_from": 2022,
        "year_to": 2024,
    }


def fixture():
    cases = []
    receipts = {}
    for i in range(4):
        scope = cohort(i)
        key = f"case-{i}"
        partial = i % 2 == 1
        status = "VERIFIED_PARTIAL_RESEARCH" if partial else "VERIFIED_RESEARCH"
        coverage = {
            "admission-history": {"missing": [], "ambiguous": [], "incomplete": []},
            "enrollment-plans": {
                "missing": [[scope["school_ids"][0], 2022]] if partial else [],
                "ambiguous": [],
                "incomplete": [],
            },
        }
        evidence = {"admission-history": [{"id": i}], "enrollment-plans": []}
        analysis = {"groups": [{"records": i + 1}]}
        cases.append({
            "case_id": key,
            "split": "development" if i < 2 else "holdout",
            "scope": scope,
            "expected_status": status,
            "expected_coverage": coverage,
            "expected_evidence_sha256": digest(evidence),
            "expected_analysis_sha256": digest(analysis),
            "source_ref": f"fixture-review-{i}",
        })
        receipts[key] = {
            "schema": "admissions-research/v1",
            "task_id": "research_" + format(i, "032x"),
            "scope": scope,
            "engine": "deepagents",
            "status": status,
            "admission_recommendation_verified": False,
            "coverage": coverage,
            "evidence": evidence,
            "evidence_sha256": digest(evidence),
            "sandbox_analysis": {
                "status": "VERIFIED_DESCRIPTIVE_ANALYSIS",
                "result": analysis,
                "result_sha256": digest(analysis),
                "cleanup_verified": True,
            },
            "provider_usage": None,
        }
    return {"schema": SCHEMA, "dataset_ref": "fixture", "review_ref": "fixture-review", "cases": cases}, receipts


def test_gold_suite_requires_complete_population_and_preserves_missing_usage():
    manifest, receipts = fixture()
    result = evaluate(manifest, receipts)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["splits"]["holdout"]["passed"] == 2
    assert result["splits"]["holdout"]["provider_usage_declared"] is None
    assert result["production_release_authorized"] is False
    assert "evidence" not in result["cases"][0]
    with pytest.raises(ValueError):
        evaluate(manifest, {"case-0": receipts["case-0"]})


def test_independently_frozen_gold_rejects_self_consistent_wrong_analysis():
    manifest, receipts = fixture()
    altered = deepcopy(receipts)
    analysis = altered["case-2"]["sandbox_analysis"]
    analysis["result"] = {"groups": [{"records": 999}]}
    analysis["result_sha256"] = digest(analysis["result"])
    result = evaluate(manifest, altered)
    assert result["status"] == "FAILED"
    assert result["splits"]["holdout"]["passed"] == 1
    assert "gold_sandbox_mismatch" in result["cases"][2]["reasons"]


def test_split_leakage_and_reused_run_rejected():
    manifest, receipts = fixture()
    manifest["cases"][2]["scope"] = {**manifest["cases"][0]["scope"], "school_ids": [102, 101]}
    with pytest.raises(ValueError):
        evaluate(manifest, receipts)
    manifest, receipts = fixture()
    receipts["case-2"]["task_id"] = receipts["case-0"]["task_id"]
    with pytest.raises(ValueError):
        evaluate(manifest, receipts)
