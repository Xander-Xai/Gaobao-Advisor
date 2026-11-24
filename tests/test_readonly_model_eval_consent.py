"""The real-model probe may never silently call an external provider in CI."""

import json
import subprocess
import sys


def test_model_probe_requires_explicit_external_call_consent(tmp_path):
    command = [
        sys.executable,
        "-m",
        "benchmarks.readonly_model_eval",
        "--out",
        str(tmp_path / "should-not-exist.json"),
        "--model",
        "fake",
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert process.returncode != 0
    assert "--permit-external-model" in process.stderr
    assert not (tmp_path / "should-not-exist.json").exists()


def test_plan_only_works_without_provider_permission(tmp_path):
    target = tmp_path / "plan.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.readonly_model_eval",
        "--out",
        str(target),
        "--model",
        "fictional-model",
        "--plan-only",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    plan = json.loads(target.read_text(encoding="utf-8"))
    assert plan["provider_called"] is False
    assert plan["controller"] == "NOT_EXECUTED"
    assert plan["planned_cases"] == 5
    assert plan["optimizer_input_eligible_if_complete"] is False
    original = target.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode != 0
    assert target.read_bytes() == original


def test_reviewed_suite_defaults_to_complete_population_and_is_order_invariant(monkeypatch):
    from benchmarks import readonly_model_eval as evaluation

    rows = [{"id": f"case-{i:02}", "split": "holdout" if i % 4 == 0 else "development"} for i in range(20)]
    reviewed = {"cases": rows, "source_sha256": "a" * 64}
    monkeypatch.setattr(evaluation, "validate_reviewed_export", lambda value: None)
    cases, plan = evaluation.build_eval_plan(reviewed, None)
    assert len(cases) == 20
    assert plan["holdout_cases"] == 5
    assert plan["optimizer_input_eligible_if_complete"] is True
    _, reordered = evaluation.build_eval_plan({**reviewed, "cases": list(reversed(rows))}, None)
    assert reordered == plan
    _, partial = evaluation.build_eval_plan(reviewed, 5)
    assert partial["groundtruth_review"] == "REVIEW_DECLARED_SUBSET"
    assert partial["optimizer_input_eligible_if_complete"] is False
    assert partial["selection_sha256"] != plan["selection_sha256"]
