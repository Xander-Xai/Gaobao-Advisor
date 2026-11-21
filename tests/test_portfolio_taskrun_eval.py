"""CI test of the durable synthetic portfolio benchmark and production gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_portfolio_taskrun_oracle_benchmark_report(tmp_path):
    target = tmp_path / "report.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["APP_ENV"] = "development"
    command = [sys.executable, "-m", "benchmarks.portfolio_taskrun_eval", "--out", str(target)]
    run = subprocess.run(command, capture_output=True, text=True, env=env, check=False, timeout=120)
    assert run.returncode == 0, run.stderr[-2500:] + run.stdout[-500:]
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["data_profile"] == "SYNTHETIC_DEMO"
    assert report["ground_truth_kind"] == "SYNTHETIC_RULES_NOT_HUMAN_REVIEWED"
    assert report["independent_oracle"] == "READ_ONLY_SQLITE_DIRECT_SNAPSHOT_RULES"
    assert report["task_runs"] == 3
    assert report["synthetic_historical_successes"] == 1
    assert report["verified_safety_abstentions"] == 1
    assert report["cancelled_before_execution"] == 1
    assert report["real_user_delivery_rate"] == "NOT_MEASURED"
    assert report["provider_tokens"] is None
    assert report["provider_cost_usd"] is None

    target.unlink()
    env["APP_ENV"] = "production"
    denied = subprocess.run(command, capture_output=True, text=True, env=env, check=False, timeout=30)
    assert denied.returncode != 0
    assert not target.exists()
