"""Reproducible process-exit fault injection for a read-only synthetic TaskRun.

Crash means a *separate worker process* exits abnormally after claiming its
lease, before any result is persisted. We advance the lease deadline in an
isolated test journal rather than sleeping 15s. Replay is at-least-once.
This does not test Deep Agents internal memory, side effects or production HA.

python -m benchmarks.taskrun_crash_recovery_eval --out /tmp/taskrun-crash.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict

_CRASH_PROCESS = """
import os
import sys
from pathlib import Path
from server.agent.taskrun_store import SyntheticTaskRunStore
store = SyntheticTaskRunStore(Path(sys.argv[1]))
_, token = store.claim(sys.argv[2], lease_seconds=15)
print(token, flush=True)
os._exit(17)  # hard process exit: no Python cleanup and no TaskRun receipt
"""


def evaluate_process_crash() -> dict[str, object]:
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise ValueError("Synthetic process-crash benchmark cannot run in production")
    with tempfile.TemporaryDirectory(prefix="gaobao-crash-bench-") as temp:
        root = Path(temp)
        journal = root / "taskrun.sqlite"
        database = root / "synthetic.sqlite"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(database)
        os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        seed_database(db_path=database, reset=True)
        store = SyntheticTaskRunStore(journal)
        request = {
            "data_profile": "SYNTHETIC_DEMO",
            "profile": {
                "school_a": DEMO_SCHOOLS[0][0],
                "school_b": DEMO_SCHOOLS[6][0],
                "major": DEMO_MAJORS[0][0],
                "province": "示例省·甲",
                "score": 550,
                "subject_type": "物理类",
                "year": DEMO_YEAR,
            },
        }
        created = store.create(request, "deterministic", "fault-operator", "crash-replay-2026")
        store.approve(created["run_id"], "fault-reviewer")
        env = dict(os.environ)
        env.pop("OPENAI_API_KEY", None)
        first = subprocess.run(
            [sys.executable, "-c", _CRASH_PROCESS, str(journal), created["run_id"]],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )
        token = first.stdout.strip()
        if first.returncode != 17 or len(token) != 32:
            raise AssertionError("Expected an isolated post-claim process crash")
        if store.get(created["run_id"])["status"] != "RUNNING":
            raise AssertionError("Process crash should leave a running lease")
        with sqlite3.connect(journal) as conn:
            conn.execute(
                "UPDATE synthetic_task_runs SET lease_deadline=? WHERE run_id=?",
                (time.time() - 1, created["run_id"]),
            )
        stale_denied = False
        try:
            store.finish(created["run_id"], token, "SUCCEEDED", {"data_profile": "SYNTHETIC_DEMO"}, None)
        except TaskRunConflict:
            stale_denied = True
        if not stale_denied:
            raise AssertionError("Expired worker unexpectedly persisted a result")
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "server.agent.taskrun_worker",
                "--journal",
                str(journal),
                "--run-id",
                created["run_id"],
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=45,
            check=False,
        )
        persisted = store.get(created["run_id"])
        recovered = (
            second.returncode == 0
            and persisted["status"] == "SUCCEEDED"
            and persisted["attempts"] == 2
            and persisted["result"] is not None
            and persisted["result"]["status"] == "evidence_complete_historical_only"
        )
        from db.database import engine

        engine.dispose()
    return {
        "controller": "DETERMINISTIC_SQL_NO_LLM",
        "data_profile": "SYNTHETIC_DEMO",
        "fault": "WORKER_ABRUPT_OS_EXIT_POST_CLAIM",
        "lease_expiry": "INJECTED_BY_TEST_SQL_NOT_REAL_CLOCK_WAIT",
        "process_crashes_injected": 1,
        "read_only_tasks_recovered": int(recovered),
        "expired_stale_receipts_blocked": int(stale_denied),
        "final_attempts": persisted["attempts"],
        "full_agent_internal_state_resume": False,
        "exactly_once_side_effect_guarantee": False,
        "production_recovery_rate": "NOT_MEASURED",
        "provider_token_cost": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if os.getenv("APP_ENV", "development").lower() == "production":
        parser.error("Production fault injection is disabled")
    report = evaluate_process_crash()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"synthetic_process_recovery={report['read_only_tasks_recovered']}/{report['process_crashes_injected']}")
    return 0 if report["read_only_tasks_recovered"] == 1 and report["expired_stale_receipts_blocked"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
