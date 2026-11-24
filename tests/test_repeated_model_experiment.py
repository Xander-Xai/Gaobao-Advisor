"""No-network contract tests for repeated, paired business/model experiments."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from benchmarks.repeated_model_experiment import _plan, _run_arm, compare_repeated_trials, main
from server.agent.decision_card_model_planner import prompt_for_variant


def fixture(run_id: str, arm: str, prompt_tokens: int = 100) -> dict:
    rows = [
        {
            "case_id": f"case-{i}",
            "split": "holdout" if i < 4 else "development",
            "passed": True,
            "safe_abstain": i % 2 == 0,
            "failure_codes": [],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 20,
            "tool_calls": 5,
            "elapsed_ms": 100.0,
        }
        for i in range(8)
    ]
    return {
        "controller": "REAL_MODEL_NOT_SCRIPTED",
        "evaluation_contract": "SYNTHETIC_DECISION_CARD_V1",
        "dataset_profile": "SYNTHETIC_DEMO",
        "human_review_complete": False,
        "comparison_eligible": True,
        "run_id": f"synthetic_eval_{run_id}",
        "model_id": "frozen-provider-model",
        "temperature": 0,
        "dataset_sha256": "a" * 64,
        "model_prompt_sha256": "b" * 64 if arm == "baseline" else "d" * 64,
        "tool_contract_sha256": "c" * 64,
        "provider_token_source": "OPENAI_COMPATIBLE_REPORTED_USAGE",
        "prompt_variant": arm,
        "cases": 8,
        "passed": 8,
        "provider_prompt_tokens": prompt_tokens * 8,
        "provider_completion_tokens": 160,
        "results": rows,
    }


def pairs(count=2):
    return [(fixture(f"old-{i}", "baseline"), fixture(f"new-{i}", "evidence_first", 90)) for i in range(count)]


def test_frozen_prompt_variants_differ_without_changing_allowed_tools():
    assert prompt_for_variant("baseline") != prompt_for_variant("evidence_first")
    assert "required_remaining" in prompt_for_variant("evidence_first")
    with pytest.raises(ValueError, match="Unknown experimental"):
        prompt_for_variant("made-up")


def test_repeated_pairs_report_quality_token_metrics_but_no_deployment():
    summary = compare_repeated_trials(pairs(3))
    assert summary["trial_count"] == 3
    assert summary["holdout_observations_per_arm"] == 12
    assert summary["baseline_holdout_verified_outcomes"] == 12
    assert summary["candidate_holdout_verified_outcomes"] == 12
    assert summary["holdout_provider_token_delta"] == -120
    assert summary["median_pair_token_delta"] == -40
    assert summary["every_trial_token_reduction"] is True
    assert summary["descriptive_candidate"] == "REVIEW_REQUIRED"
    assert summary["production_deployment_authorized"] is False
    assert summary["statistical_significance"] == "NOT_ESTABLISHED"
    assert summary["provenance_independently_verified"] is False
    assert summary["provider_cost_usd"] is None
    assert summary["real_admissions_accuracy"] == "NOT_MEASURED"


def test_repeated_pairs_reject_quality_regression_even_if_cheaper():
    cases = pairs()
    cases[0][1]["results"][0]["passed"] = False
    cases[0][1]["passed"] = 7
    summary = compare_repeated_trials(cases)
    assert summary["descriptive_candidate"] == "REJECT_CANDIDATE"
    assert summary["regressions"] == ["trial-1:case-0"]


@pytest.mark.parametrize("problem", ["reused", "model_drift", "prompt_drift", "tool_drift", "fake", "arm_swap"])
def test_repeated_pairs_reject_incompatible_or_fabricated_reports(problem):
    cases = pairs()
    if problem == "reused":
        cases[1][0]["run_id"] = cases[0][0]["run_id"]
    elif problem == "model_drift":
        for report in cases[1]:
            report["model_id"] = "other-model"
    elif problem == "prompt_drift":
        cases[1][1]["model_prompt_sha256"] = "f" * 64
    elif problem == "tool_drift":
        cases[1][0]["tool_contract_sha256"] = "f" * 64
    elif problem == "fake":
        cases[1][1]["controller"] = "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    elif problem == "arm_swap":
        cases[1][0]["prompt_variant"] = "evidence_first"
    with pytest.raises(ValueError):
        compare_repeated_trials(cases)


def test_missing_trials_or_duplicate_arms_rejected():
    with pytest.raises(ValueError, match="2..5"):
        compare_repeated_trials(pairs(1))
    cases = pairs()
    cases[0][1]["model_prompt_sha256"] = cases[0][0]["model_prompt_sha256"]
    with pytest.raises(ValueError, match="actually differ"):
        compare_repeated_trials(cases)


def test_default_dry_run_uses_no_network_or_files(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    directory = tmp_path / "must-not-create"
    assert main(["--model", "test", "--out-dir", str(directory)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "DRY_RUN_NO_NETWORK"
    assert report["maximum_possible_model_decisions"] == 256
    assert report["provider_usd_upper_bound"] == "UNKNOWN"
    assert not directory.exists()
    assert _plan(2, "test")["total_synthetic_case_executions"] == 32


def test_paid_execution_requires_double_optin_and_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = ["--model", "test", "--out-dir", str(tmp_path / "runs"), "--execute"]
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(args + ["--permit-external-model", "--confirm-provider-charges"])
    assert exc.value.code == 2
    assert not (tmp_path / "runs").exists()


def test_subprocess_wrapper_validates_full_report_even_on_case_failures(monkeypatch, tmp_path):
    report = fixture("fake-not-actual-model", "baseline")
    report["results"][0]["passed"] = False
    report["passed"] = 7

    def fake_run(args, **kwargs):
        assert "--permit-external-model" in args
        assert "--prompt-variant" in args
        assert kwargs["capture_output"] is True
        output = tmp_path / "report.json"
        output.write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("benchmarks.repeated_model_experiment.subprocess.run", fake_run)
    assert _run_arm("frozen-provider-model", "baseline", tmp_path / "report.json", 900)["passed"] == 7
    invalid = copy.deepcopy(report)
    invalid["provider_prompt_tokens"] = 0

    def bad_run(args, **kwargs):
        (tmp_path / "report.json").write_text(json.dumps(invalid), encoding="utf-8")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("benchmarks.repeated_model_experiment.subprocess.run", bad_run)
    with pytest.raises(ValueError, match="Inconsistent prompt"):
        _run_arm("frozen-provider-model", "baseline", tmp_path / "report.json", 900)


def test_paid_preflight_refuses_nonempty_output_directory_without_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key-not-used")
    directory = tmp_path / "occupied"
    directory.mkdir()
    existing = directory / "trial-1-baseline.json"
    existing.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--model",
                "test",
                "--trials",
                "2",
                "--out-dir",
                str(directory),
                "--execute",
                "--permit-external-model",
                "--confirm-provider-charges",
            ]
        )
    assert exc.value.code == 2
    assert existing.read_text(encoding="utf-8") == "do not overwrite"


def test_paid_preflight_rejects_excessive_trials_before_any_provider_call(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--model", "test", "--out-dir", str(tmp_path / "runs"), "--trials", "6"])
    assert exc.value.code == 2


def test_dry_run_exposes_full_experiment_budget(tmp_path, capsys):
    assert (
        main(
            [
                "--model",
                "test",
                "--out-dir",
                str(tmp_path / "runs"),
                "--max-provider-calls-per-arm",
                "40",
                "--stop-after-reported-tokens-per-arm",
                "10000",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["aggregate_request_cap"] == 160
    assert plan["sum_of_reported_token_stop_thresholds"] == 40_000
    assert plan["hard_token_cap"] is False
    assert not (tmp_path / "runs").exists()


def test_paid_run_stops_after_first_missing_budget_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key-not-used")
    calls = []

    def fake_arm(model, arm, output, timeout_seconds, **kwargs):
        calls.append((arm, kwargs))
        return fixture("not-a-real-run", arm)

    monkeypatch.setattr("benchmarks.repeated_model_experiment._run_arm", fake_arm)
    with pytest.raises(ValueError, match="reconciled provider budget"):
        main(
            [
                "--model",
                "frozen-provider-model",
                "--out-dir",
                str(tmp_path / "runs"),
                "--execute",
                "--permit-external-model",
                "--confirm-provider-charges",
                "--max-provider-calls-per-arm",
                "40",
                "--stop-after-reported-tokens-per-arm",
                "10000",
            ]
        )
    assert calls == [("baseline", {"max_calls": 40, "stop_tokens": 10000})]
    assert not (tmp_path / "runs" / "repeated-experiment-summary.json").exists()
