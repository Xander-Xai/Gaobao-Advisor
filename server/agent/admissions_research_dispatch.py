"""Bounded scheduled worker: dispatch approved real admissions research only.

Invoke from a trusted operator scheduler, not the HTTP server. No automatic
retry of paid tasks. One launch processes at most the explicit task budget.
"""

from __future__ import annotations

import argparse
import os

from server.agent.admissions_research_taskrun import (
    ResearchConflict,
    canonical,
    configured_dataset,
    execute_approved,
    open_configured_journal,
)


def dispatch(journal, dataset_ref, model_id, image_id, session_factory, *, max_tasks, execute=execute_approved):
    if type(max_tasks) is not int or not 1 <= max_tasks <= 3:
        raise ValueError("Dispatch budget must be 1..3 tasks")
    candidates = journal.approved_candidates(dataset_ref, model_id, image_id)
    results = []
    # Count attempts, not successes: a failed or cancelled execution must not
    # silently expand the operator's maximum paid exposure.
    for run_id in candidates[:max_tasks]:
        try:
            result = execute(journal, run_id, dataset_ref, session_factory)
            results.append({"run_id": run_id, "status": result["status"]})
        except ResearchConflict:
            results.append({"run_id": run_id, "status": "NOT_DISPATCHED_OR_LEASE_LOST"})
    return {
        "schema": "approved-admissions-dispatch/v1",
        "attempt_budget": max_tasks,
        "attempted": len(results),
        "results": results,
        "automatic_paid_replay": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--max-tasks", type=int, choices=range(1, 4), required=True)
    parser.add_argument("--authorize-model-call", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not os.getenv("DATABASE_URL") or not os.getenv("OPENAI_API_KEY"):
        parser.error("Explicit approved data and model credentials required")
    from db.database import get_session

    report = dispatch(
        open_configured_journal(),
        configured_dataset(),
        args.model,
        args.image,
        get_session,
        max_tasks=args.max_tasks,
    )
    print(canonical(report))
    return 0 if all(row["status"] in {"SUCCEEDED", "NEEDS_REVIEW"} for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
