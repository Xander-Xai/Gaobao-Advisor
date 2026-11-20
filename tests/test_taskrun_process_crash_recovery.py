"""Worker crash is a real subprocess exit, with synthetic lease expiry injection."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_abrupt_worker_exit_is_recovered_by_new_process_with_sql_verified_receipt(tmp_path):
    output = tmp_path / "recovery.json"
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["APP_ENV"] = "development"
    run = subprocess.run(
        [sys.executable, "-m", "benchmarks.taskrun_crash_recovery_eval", "--out", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr[-3000:] + run.stdout[-500:]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["process_crashes_injected"] == 1
    assert report["read_only_tasks_recovered"] == 1
    assert report["expired_stale_receipts_blocked"] == 1
    assert report["final_attempts"] == 2
    assert report["fault"] == "WORKER_ABRUPT_OS_EXIT_POST_CLAIM"
    assert report["lease_expiry"] == "INJECTED_BY_TEST_SQL_NOT_REAL_CLOCK_WAIT"
    assert report["full_agent_internal_state_resume"] is False
    assert report["exactly_once_side_effect_guarantee"] is False
    assert report["production_recovery_rate"] == "NOT_MEASURED"
    assert report["provider_token_cost"] is None


def test_production_mode_disables_process_fault_injection(tmp_path):
    output = tmp_path / "forbidden.json"
    env = dict(os.environ)
    env["APP_ENV"] = "production"
    run = subprocess.run(
        [sys.executable, "-m", "benchmarks.taskrun_crash_recovery_eval", "--out", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )
    assert run.returncode != 0
    assert not output.exists()
