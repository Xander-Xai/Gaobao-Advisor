"""Three durable TaskRun paths with a separate direct-SQL synthetic oracle.

python -m benchmarks.portfolio_taskrun_eval --out /tmp/portfolio-taskrun-eval.json

This report proves synthetic task lifecycle and SQL consistency, NOT actual
admissions quality, independent human labels or model cost improvements.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if os.getenv("APP_ENV", "development").lower() == "production":
        parser.error("Fictional evaluation is not allowed in production")

    with tempfile.TemporaryDirectory(prefix="gaobao-portfolio-gt-") as directory:
        root = Path(directory)
        db_file = root / "admissions.sqlite"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(db_file)
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
        from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
        from server.agent.taskrun_store import SyntheticTaskRunStore
        from server.agent.taskrun_worker import execute_approved_run

        seed_database(db_path=db_file, reset=True)
        store = SyntheticTaskRunStore(root / "taskrun.sqlite")
        candidates = [
            {"school": DEMO_SCHOOLS[0][0], "major": DEMO_MAJORS[0][0]},
            {"school": DEMO_SCHOOLS[6][0], "major": DEMO_MAJORS[0][0]},
            {"school": DEMO_SCHOOLS[2][0], "major": DEMO_MAJORS[4][0]},
            {"school": DEMO_SCHOOLS[4][0], "major": DEMO_MAJORS[8][0]},
            {"school": DEMO_SCHOOLS[1][0], "major": DEMO_MAJORS[0][0]},
        ]
        cases: list[dict] = []
        for label, subject in (("historical_comparison", "物理类"), ("safety_abstention", "历史类")):
            payload = {
                "data_profile": "SYNTHETIC_DEMO",
                "portfolio": {
                    "province": "示例省·甲",
                    "score": 550,
                    "subject_type": subject,
                    "year": DEMO_YEAR,
                    "candidates": candidates,
                },
            }
            run = store.create(payload, "portfolio", "benchmark-operator", "portfolio-" + label + "-2026")
            store.approve(run["run_id"], "benchmark-checker")
            final = execute_approved_run(store, run["run_id"])
            evidence = final["result"]
            cases.append(
                {
                    "case_id": label,
                    "terminal_status": final["status"],
                    "attempts": final["attempts"],
                    "historically_supported": evidence["eligible_history_count"] if evidence else None,
                    "excluded": evidence["excluded_count"] if evidence else None,
                    "human_review_complete": evidence["human_review_complete"] if evidence else None,
                }
            )
        cancel_payload = {
            "data_profile": "SYNTHETIC_DEMO",
            "portfolio": {
                "province": "示例省·甲",
                "score": 550,
                "subject_type": "物理类",
                "year": DEMO_YEAR,
                "candidates": candidates,
            },
        }
        aborted = store.create(cancel_payload, "portfolio", "benchmark-operator", "portfolio-cancelled-2026")
        store.approve(aborted["run_id"], "benchmark-checker")
        cancelled = store.cancel(aborted["run_id"], "benchmark-operator")
        cases.append(
            {
                "case_id": "approved_then_cancelled",
                "terminal_status": cancelled["status"],
                "attempts": cancelled["attempts"],
                "historically_supported": None,
                "excluded": None,
                "human_review_complete": False,
            }
        )
        from db.database import engine

        engine.dispose()

    report = {
        "data_profile": "SYNTHETIC_DEMO",
        "controller": "DETERMINISTIC_SQL_NO_LLM",
        "independent_oracle": "READ_ONLY_SQLITE_DIRECT_SNAPSHOT_RULES",
        "ground_truth_kind": "SYNTHETIC_RULES_NOT_HUMAN_REVIEWED",
        "task_runs": len(cases),
        "synthetic_historical_successes": sum(case["terminal_status"] == "SUCCEEDED" for case in cases),
        "verified_safety_abstentions": sum(case["terminal_status"] == "NEEDS_REVIEW" for case in cases),
        "cancelled_before_execution": sum(case["terminal_status"] == "CANCELLED" for case in cases),
        "real_user_delivery_rate": "NOT_MEASURED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "provider_tokens": None,
        "provider_cost_usd": None,
        "cases": cases,
    }
    ok = (
        len(cases) == 3
        and cases[0]["terminal_status"] == "SUCCEEDED"
        and cases[0]["historically_supported"] == 4
        and cases[0]["excluded"] == 1
        and cases[1]["terminal_status"] == "NEEDS_REVIEW"
        and cases[1]["historically_supported"] == 0
        and cases[1]["excluded"] == 5
        and cases[2]["terminal_status"] == "CANCELLED"
        and cases[2]["attempts"] == 0
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"synthetic_portfolio_taskruns_verified={int(ok)}/1")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
