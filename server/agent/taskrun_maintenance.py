"""Explicit, bounded recovery of abandoned synthetic TaskRuns.

Usage: python -m server.agent.taskrun_maintenance --journal /tmp/tasks.db
This never starts an Agent, modifies admissions data, or calls an LLM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_worker import _gate_execution


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--max-runs", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        _gate_execution("deterministic", permit_external_model=False, model=None)
        recovered = SyntheticTaskRunStore(args.journal).reconcile_exhausted(max_runs=args.max_runs)
    except (ValueError, RuntimeError, LookupError):
        parser.error("Synthetic maintenance disabled or invalid journal/batch")
    print(
        json.dumps(
            {
                "data_profile": "SYNTHETIC_DEMO",
                "action": "RECONCILE_EXHAUSTED_LEASES",
                "terminalized_runs": recovered,
                "external_model_executed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
