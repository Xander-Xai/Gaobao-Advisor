"""Business evaluations exercise independent SQL truth without cloud access."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarks.decision_card_business_eval import ProviderBudget, frozen_cases

BASE = [sys.executable, "-m", "benchmarks.decision_card_business_eval"]


def test_suite_is_frozen_review_limited_and_partitioned():
    cases = frozen_cases()
    assert len(cases) == 8
    assert len({item["case_id"] for item in cases}) == 8
    assert sum(item["split"] == "holdout" for item in cases) == 4
    assert {item["expected_status"] for item in cases} == {
        "evidence_complete_historical_only",
        "needs_review",
    }
    assert all(item["data_profile"] == "SYNTHETIC_DEMO" for item in cases)


def test_real_sql_baseline_eight_cases_independently_scored(tmp_path):
    output = tmp_path / "baseline.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    process = subprocess.run(
        [*BASE, "--out", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=110,
        env=env,
    )
    assert process.returncode == 0, process.stderr[-2100:] + process.stdout[-800:]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["cases"] == 8
    assert report["passed"] == 8
    assert report["holdout_cases"] == 4
    assert report["holdout_passed"] == 4
    assert report["controller"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert report["human_review_complete"] is False
    assert report["provider_prompt_tokens"] is None
    assert report["provider_completion_tokens"] is None
    assert report["real_admissions_accuracy"] == "NOT_MEASURED"
    assert report["evaluation_contract"] == "SYNTHETIC_DECISION_CARD_V1"
    assert report["run_id"].startswith("synthetic_eval_")
    assert report["model_prompt_sha256"] is None
    assert len(report["tool_contract_sha256"]) == 64
    assert report["comparison_eligible"] is False
    assert report["provider_cost_usd"] is None
    assert len(report["dataset_sha256"]) == 64
    assert all(row["passed"] and not row["failure_codes"] for row in report["results"])


def test_real_model_mode_requires_explicit_permission_and_api_key(tmp_path):
    output = tmp_path / "paid.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    for flags, marker in [
        (["--mode", "model", "--model", "explicit-model"], "--permit-external-model"),
        (["--mode", "model", "--model", "explicit-model", "--permit-external-model"], "OPENAI_API_KEY"),
        (["--mode", "baseline", "--model", "not-allowed"], "Baseline mode"),
    ]:
        process = subprocess.run(
            [*BASE, "--out", str(output), *flags],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        assert process.returncode != 0
        assert marker in process.stderr
        assert not output.exists()


def _budget_client(*responses):
    pending = iter(responses)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        value = next(pending)
        if isinstance(value, Exception):
            raise value
        return value

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def _usage_response(prompt=10, completion=5):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def test_budget_counts_usage_before_planner_parses_response_and_caps_requests():
    client, calls = _budget_client(_usage_response())
    budget = ProviderBudget(client, max_calls=1, stop_after_tokens=100)
    budget.chat.completions.create(model="fixture")
    with pytest.raises(RuntimeError, match="budget stopped"):
        budget.chat.completions.create(model="fixture")
    assert len(calls) == 1
    assert budget.receipt()["known_prompt_tokens"] == 10
    assert budget.receipt()["known_completion_tokens"] == 5
    assert budget.receipt()["stop_reason"] == "request_cap_reached"


def test_budget_threshold_is_post_response_not_hard_token_cap():
    client, calls = _budget_client(_usage_response())
    budget = ProviderBudget(client, max_calls=5, stop_after_tokens=12)
    budget.create()
    with pytest.raises(RuntimeError, match="budget stopped"):
        budget.create()
    assert len(calls) == 1
    assert budget.receipt()["stop_reason"] == "reported_token_threshold_reached"
    assert budget.receipt()["hard_token_cap"] is False


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (SimpleNamespace(usage=None), "provider_usage_unknown"),
        (_usage_response(prompt=True), "provider_usage_unknown"),
        (TimeoutError("fixture"), "provider_error_usage_unknown"),
    ],
)
def test_budget_unknown_usage_stops_all_future_calls(response, reason):
    client, calls = _budget_client(response)
    budget = ProviderBudget(client, max_calls=5, stop_after_tokens=100)
    with pytest.raises((RuntimeError, TimeoutError)):
        budget.create()
    with pytest.raises(RuntimeError, match="budget stopped"):
        budget.create()
    assert len(calls) == 1
    assert budget.receipt()["stop_reason"] == reason
    assert budget.receipt()["usage_complete"] is False


def test_existing_eval_evidence_is_not_overwritten(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("prior evidence", encoding="utf-8")
    process = subprocess.run([*BASE, "--out", str(output)], capture_output=True, text=True, timeout=15)
    assert process.returncode != 0
    assert "new file" in process.stderr
    assert output.read_text(encoding="utf-8") == "prior evidence"
