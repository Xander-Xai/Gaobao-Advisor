"""Offline end-to-end synthetic TaskRun business lifecycle evaluation.

Unlike the lower-level decision-card benchmark, this executes the real
create -> separate approval -> claimed worker -> SQL verifier -> persisted
receipt path. No real students, paid models, or remote network calls.

python -m benchmarks.taskrun_lifecycle_eval --out /tmp/taskrun-lifecycle.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmarks.decision_card_business_eval import _digest, frozen_cases, grade_business_case
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_worker import execute_approved_run

_CONTROLLER = "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"


def evaluate_taskruns(store: SyntheticTaskRunStore, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Exercise real durable TaskRuns and score against independent synthetic SQL."""
    reports: list[dict[str, Any]] = []
    for case in cases:
        start = time.monotonic()
        request = {"data_profile": "SYNTHETIC_DEMO", "profile": case["profile"]}
        key = "taskrun-eval-" + case["case_id"] + "-2026"
        created = store.create(request, "deterministic", "eval-operator", key)
        idempotent = store.create(request, "deterministic", "eval-operator", key)
        if created["run_id"] != idempotent["run_id"]:
            raise AssertionError("Idempotent task creation returned a different run")
        approved = store.approve(created["run_id"], "eval-reviewer")
        terminal = execute_approved_run(store, created["run_id"])
        persisted = store.get(created["run_id"], requester="eval-operator")
        card = persisted.get("result")
        assessment = (
            grade_business_case(case, card)
            if isinstance(card, dict)
            else {"passed": False, "failure_codes": ["missing_persisted_card"]}
        )
        expected_terminal = (
            "SUCCEEDED" if case["expected_status"] == "evidence_complete_historical_only" else "NEEDS_REVIEW"
        )
        execution = card.get("execution", {}) if isinstance(card, dict) else {}
        safe_refusal = (
            expected_terminal == "NEEDS_REVIEW"
            and persisted["status"] == "NEEDS_REVIEW"
            and isinstance(card, dict)
            and card.get("reason") == "explicit_abstention"
            and card.get("observed_negative_evidence_count", 0) >= 1
            and card.get("schools") == []
            and card.get("evidence_ids") == []
        )
        valid = (
            assessment["passed"]
            and created["status"] == "PENDING_APPROVAL"
            and approved["status"] == "APPROVED"
            and approved["approved_by"] != created["owner"]
            and terminal["status"] == expected_terminal
            and persisted["status"] == expected_terminal
            and persisted["attempts"] == 1
            and persisted["result"] == terminal["result"]
            and ((expected_terminal == "SUCCEEDED" and card.get("status") == case["expected_status"]) or safe_refusal)
        )
        failures = list(assessment["failure_codes"])
        if not valid and not failures:
            failures.append("taskrun_lifecycle_contract")
        reports.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "expected_terminal": expected_terminal,
                "actual_terminal": persisted["status"],
                "verified_outcome_passed": bool(valid),
                "historical_comparison_delivered": bool(valid and expected_terminal == "SUCCEEDED"),
                "safe_abstention": bool(valid and safe_refusal),
                "failure_codes": failures,
                "attempts": persisted["attempts"],
                "tool_calls": execution.get("tool_calls"),
                "deterministic_controller_steps": execution.get("model_steps"),
                "provider_prompt_tokens": None,
                "provider_completion_tokens": None,
                "provider_cost_usd": None,
                "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
            }
        )

    latencies = sorted(row["elapsed_ms"] for row in reports)
    # Nearest-rank empirical P95 for this tiny local test sample only.
    p95_ms = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    calls = [row["tool_calls"] for row in reports]
    return {
        "suite": "TASKRUN_LIFECYCLE_SYNTHETIC_V1",
        "dataset_profile": "SYNTHETIC_DEMO",
        "controller": _CONTROLLER,
        "evaluation_scope": "OFFLINE_DETERMINISTIC_ENGINE_WITH_REAL_SQL_AND_JOURNAL",
        "dataset_sha256": _digest(cases),
        "human_review_complete": False,
        "cases": len(reports),
        "verified_outcomes": sum(row["verified_outcome_passed"] for row in reports),
        "historical_comparisons_delivered": sum(row["historical_comparison_delivered"] for row in reports),
        "safe_abstentions": sum(row["safe_abstention"] for row in reports),
        "holdout_cases": sum(row["split"] == "holdout" for row in reports),
        "holdout_verified_outcomes": sum(
            row["verified_outcome_passed"] for row in reports if row["split"] == "holdout"
        ),
        "tool_calls": sum(calls) if all(type(value) is int for value in calls) else None,
        "empirical_local_p95_ms": p95_ms,
        "provider_prompt_tokens": None,
        "provider_completion_tokens": None,
        "provider_cost_usd": None,
        "model_autonomous_task_success_rate": "NOT_MEASURED",
        "real_user_north_star_metric": "NOT_MEASURED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "note": "A correct SQL-supported abstention is NOT a delivered comparison or real user completion.",
        "results": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=8)
    args = parser.parse_args(argv)
    if not 1 <= args.max_cases <= 8:
        parser.error("--max-cases must be 1..8")
    if os.getenv("APP_ENV", "development").lower() == "production":
        parser.error("Synthetic evaluation is disabled in production")
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
    cases = frozen_cases()[: args.max_cases]
    with tempfile.TemporaryDirectory(prefix="gaobao-taskrun-eval-") as root:
        home = Path(root)
        path = home / "admissions.sqlite"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(path)
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=path, reset=True)
        store = SyntheticTaskRunStore(home / "journal.sqlite")
        report = evaluate_taskruns(store, cases)
        from db.database import engine

        engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("synthetic_taskrun_verified_outcomes=" + str(report["verified_outcomes"]) + "/" + str(report["cases"]))
    return 0 if report["verified_outcomes"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
