"""TaskRun worker: business approval is upstream of any Agent execution.

CLI executes one approved synthetic run. Deep Agents requires explicit model
permission, an API key and the optional SDK. The worker never accepts
free-form prompts or model-selected SQL, and records sanitized receipts only.

Replay after a lost lease is at-least-once for read-only SQL tasks, NOT a
durable Deep Agents token-level continuation or exactly-once side-effect run.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card
from server.agent.taskrun_store import SyntheticTaskRunStore


def _gate_execution(engine: str, *, permit_external_model: bool, model: str | None) -> None:
    if (
        os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
        or os.getenv("APP_ENV", "development").lower() == "production"
        or os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false"
    ):
        raise ValueError("Synthetic Agent worker disabled or unsafe environment")
    if engine == "deepagents":
        if (
            not permit_external_model
            or not model
            or not model.startswith("openai:")
            or not os.getenv("OPENAI_API_KEY")
        ):
            raise ValueError("Deep Agents requires explicit model authorization and provider key")
    elif permit_external_model or model:
        raise ValueError("Deterministic mode must not request external model access")


def execute_approved_run(
    store: SyntheticTaskRunStore,
    run_id: str,
    *,
    permit_external_model: bool = False,
    model: str | None = None,
    agent_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Claim, execute and commit one approved task, never bypass approval."""
    current = store.get(run_id)
    engine = current["engine"]
    _gate_execution(engine, permit_external_model=permit_external_model, model=model)
    claimed, token = store.claim(run_id, lease_seconds=600 if engine == "deepagents" else 120)
    profile = DecisionCardProfile(**claimed["payload"]["profile"])
    try:
        if engine == "deepagents":
            if agent_runner is None:
                from server.agent.deepagents_adapter import run_deepagents_research

                agent_runner = run_deepagents_research
            card = agent_runner(profile, model, task_id=run_id)
        else:
            card = build_synthetic_decision_card(profile)
        if card.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Agent returned non-synthetic output")
        status = (
            "SUCCEEDED"
            if card.get("status") == "evidence_complete_historical_only"
            else "NEEDS_REVIEW"
        )
        failure = None if status == "SUCCEEDED" else "insufficient_verified_evidence"
        return store.finish(run_id, token, status, card, failure)
    except Exception:  # noqa: BLE001 - never persist model/SQL/stack trace errors
        return store.finish(run_id, token, "FAILED", None, "agent_execution_failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", help="Explicit openai:MODEL_NAME for optional Deep Agents")
    parser.add_argument("--permit-external-model", action="store_true")
    args = parser.parse_args(argv)
    store = SyntheticTaskRunStore(args.journal)
    try:
        result = execute_approved_run(
            store,
            args.run_id,
            permit_external_model=args.permit_external_model,
            model=args.model,
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        # Avoid printing potentially private database paths or credentials.
        parser.error(type(exc).__name__ + ": synthetic task not executable")
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "status": result["status"],
                "engine": result["engine"],
                "attempts": result["attempts"],
                "failure_code": result["failure_code"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] in {"SUCCEEDED", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
