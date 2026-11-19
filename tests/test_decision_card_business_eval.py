"""Business evaluations exercise independent SQL truth without cloud access."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from benchmarks.decision_card_business_eval import frozen_cases

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

