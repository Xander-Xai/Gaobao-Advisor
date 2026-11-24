"""Paired model evaluation refuses scripted, incomplete and unreviewed promotion."""

import copy
import json

import pytest

from benchmarks.model_run_comparison import compare_model_runs, main


def fixture(run_id):
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
                "elapsed_ms": 100.0,
            }
        )
    return {
        "controller": "REAL_MODEL_NOT_SCRIPTED",
        "evaluation_contract": "SYNTHETIC_DECISION_CARD_V1",
        "dataset_profile": "SYNTHETIC_DEMO",
        "human_review_complete": False,
        "comparison_eligible": True,
        "run_id": "synthetic_eval_" + run_id,
        "model_id": "test-provider-model",
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


def test_paired_holdout_describes_delta_but_never_authorizes_production(tmp_path):
    base, candidate = fixture("baseline"), fixture("candidate")
    for row in candidate["results"]:
        row["prompt_tokens"] = 90
        row["tool_calls"] = 4
    candidate["provider_prompt_tokens"] = 720
    result = compare_model_runs(base, candidate)
    assert result["groups"]["holdout"]["baseline_verified_outcomes"] == 4
    assert result["groups"]["holdout"]["candidate_verified_outcomes"] == 4
    assert result["groups"]["holdout"]["provider_token_delta"] == -40
    assert result["descriptive_candidate"] == "REVIEW_REQUIRED"
    assert result["production_deployment_authorized"] is False
    assert result["provenance_independently_verified"] is False
    assert result["provider_cost_usd"] is None
    assert result["statistical_significance"] == "NOT_ESTABLISHED"
    baseline_path, candidate_path, out = (tmp_path / name for name in ("b.json", "c.json", "diff.json"))
    baseline_path.write_text(json.dumps(base), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    assert main(["--baseline", str(baseline_path), "--candidate", str(candidate_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["production_deployment_authorized"] is False


def test_paired_holdout_rejects_case_regression_even_with_lower_tokens():
    base, candidate = fixture("baseline"), fixture("candidate")
    candidate["results"][0]["passed"] = False
    candidate["passed"] = 7
    for row in candidate["results"]:
        row["prompt_tokens"] = 90
    candidate["provider_prompt_tokens"] = 720
    report = compare_model_runs(base, candidate)
    assert report["descriptive_candidate"] == "REJECT_CANDIDATE"
    assert report["groups"]["holdout"]["regressed_case_ids"] == ["case-0"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("controller", "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"),
        ("comparison_eligible", False),
        ("human_review_complete", True),
        ("provider_token_source", "NOT_MEASURED"),
        ("provider_prompt_tokens", None),
        ("temperature", 1),
        ("evaluation_contract", "UNKNOWN"),
    ],
)
def test_model_comparison_rejects_unverifiable_or_incompatible_report(field, value):
    baseline = fixture("baseline")
    candidate = fixture("candidate")
    candidate[field] = value
    with pytest.raises(ValueError):
        compare_model_runs(baseline, candidate)


def test_model_comparison_rejects_dataset_tool_model_or_case_changes():
    baseline = fixture("baseline")
    for key, value in (
        ("dataset_sha256", "f" * 64),
        ("tool_contract_sha256", "f" * 64),
        ("model_id", "different-model"),
    ):
        candidate = fixture("candidate")
        candidate[key] = value
        with pytest.raises(ValueError, match="Model, fixture"):
            compare_model_runs(baseline, candidate)
    candidate = fixture("candidate")
    candidate["results"][0]["case_id"] = "different-case"
    with pytest.raises(ValueError, match="Case identities"):
        compare_model_runs(baseline, candidate)
    candidate = fixture("candidate")
    candidate["results"][0]["split"] = "development"
    with pytest.raises(ValueError):
        compare_model_runs(baseline, candidate)
    candidate = fixture("candidate")
    candidate["results"][0]["prompt_tokens"] = None
    with pytest.raises(ValueError, match="provider usage"):
        compare_model_runs(baseline, candidate)


def test_same_run_and_fake_token_totals_are_rejected():
    base = fixture("same")
    with pytest.raises(ValueError, match="paired with itself"):
        compare_model_runs(base, copy.deepcopy(base))
    other = fixture("another")
    other["provider_completion_tokens"] = 0
    with pytest.raises(ValueError, match="Inconsistent completion"):
        compare_model_runs(base, other)


def budget_fixture(run_id):
    report = fixture(run_id)
    report["requested_cases"] = 8
    report["unattempted_case_ids"] = []
    report["provider_usage_reconciled"] = True
    report["provider_budget"] = {
        "max_calls": 64,
        "stop_after_reported_tokens": 120000,
        "attempts": 48,
        "known_usage_responses": 48,
        "known_prompt_tokens": 800,
        "known_completion_tokens": 160,
        "usage_complete": True,
        "stop_reason": None,
        "hard_token_cap": False,
    }
    return report


def test_budget_receipts_must_match_comparison_configuration():
    baseline, candidate = budget_fixture("baseline"), budget_fixture("candidate")
    assert compare_model_runs(baseline, candidate)["provider_budget_contract_checked"] is True
    candidate["provider_budget"]["max_calls"] = 80
    with pytest.raises(ValueError, match="budget contracts differ"):
        compare_model_runs(baseline, candidate)
    with pytest.raises(ValueError, match="budget contracts differ"):
        compare_model_runs(baseline, fixture("legacy"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("known_usage_responses", 47),
        ("known_prompt_tokens", 801),
        ("usage_complete", False),
        ("stop_reason", "request_cap_reached"),
        ("attempts", True),
        ("max_calls", 48),
    ],
)
def test_budget_receipt_rejects_incomplete_or_inconsistent_accounting(field, value):
    candidate = budget_fixture("candidate")
    candidate["provider_budget"][field] = value
    with pytest.raises(ValueError, match="budget"):
        compare_model_runs(budget_fixture("baseline"), candidate)


def test_legacy_reports_are_explicitly_not_budget_checked():
    result = compare_model_runs(fixture("baseline"), fixture("candidate"))
    assert result["provider_budget_contract_checked"] is False


def test_comparison_cannot_overwrite_input_evidence(tmp_path):
    baseline, candidate = tmp_path / "base.json", tmp_path / "candidate.json"
    baseline.write_text(json.dumps(fixture("baseline")), encoding="utf-8")
    candidate.write_text(json.dumps(fixture("candidate")), encoding="utf-8")
    before = baseline.read_bytes()
    with pytest.raises(FileExistsError):
        main(["--baseline", str(baseline), "--candidate", str(candidate), "--out", str(baseline)])
    assert baseline.read_bytes() == before
