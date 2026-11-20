"""The offline 8-case report must use durable TaskRuns, not only a planner fake."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_taskrun_lifecycle_eight_case_report_uses_real_sql(tmp_path):
    out = tmp_path / "lifecycle.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["APP_ENV"] = "development"
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.taskrun_lifecycle_eval", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-800:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["suite"] == "TASKRUN_LIFECYCLE_SYNTHETIC_V1"
    assert report["controller"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert report["cases"] == report["verified_outcomes"] == 8
    assert report["holdout_cases"] == report["holdout_verified_outcomes"] == 4
    assert report["historical_comparisons_delivered"] == 4
    assert report["safe_abstentions"] == 4
    assert report["provider_prompt_tokens"] is None
    assert report["provider_completion_tokens"] is None
    assert report["provider_cost_usd"] is None
    assert report["model_autonomous_task_success_rate"] == "NOT_MEASURED"
    assert report["real_user_north_star_metric"] == "NOT_MEASURED"
    assert report["human_review_complete"] is False
    assert report["tool_calls"] is not None and report["tool_calls"] > 0
    assert all(item["attempts"] == 1 for item in report["results"])
    assert all(item["verified_outcome_passed"] for item in report["results"])
    assert all(item["provider_cost_usd"] is None for item in report["results"])
    assert all("run_id" not in item for item in report["results"])


def test_taskrun_lifecycle_rejects_production_and_requires_bounded_case_count(tmp_path):
    env = dict(os.environ)
    env["APP_ENV"] = "production"
    out = tmp_path / "should-not-write.json"
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.taskrun_lifecycle_eval", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert proc.returncode != 0
    assert not out.exists()
    env["APP_ENV"] = "development"
    invalid = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.taskrun_lifecycle_eval",
            "--out",
            str(out),
            "--max-cases",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert invalid.returncode != 0
    assert not out.exists()
