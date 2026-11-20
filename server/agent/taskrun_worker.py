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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card
from server.agent.taskrun_receipt_verifier import verify_taskrun_receipt
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict
from server.observability.agent_trace import continue_trace, safe_agent_span


def _gate_execution(engine: str, *, permit_external_model: bool, model: str | None) -> None:
    if (
        os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
        or os.getenv("APP_ENV", "development").lower() == "production"
        or os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false"
    ):
        raise ValueError("Synthetic Agent worker disabled or unsafe environment")
    if engine == "deepagents":
        if not permit_external_model or not model or not model.startswith("openai:") or not os.getenv("OPENAI_API_KEY"):
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
    """Continue persisted synthetic W3C context across worker processes."""
    with continue_trace(store.get_internal_traceparent(run_id)):
        with safe_agent_span("agent.taskrun", {"agent.engine": store.get(run_id)["engine"]}):
            return _execute_approved_run(
                store, run_id, permit_external_model=permit_external_model,
                model=model, agent_runner=agent_runner,
            )


def _execute_approved_run(
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
    configured_checkpoint = os.getenv("SYNTHETIC_DEEPAGENTS_CHECKPOINT_DB", "")
    if engine == "deepagents" and configured_checkpoint and Path(configured_checkpoint).resolve() == store.path:
        # The CLI supplies --journal but does not necessarily export its path
        # as an environment variable. Refuse mixing graph state with the
        # business TaskRun CAS journal even in that configuration.
        raise ValueError("Deep Agents graph checkpoint must not share the TaskRun journal")
    _gate_execution(engine, permit_external_model=permit_external_model, model=model)
    claimed, token = store.claim(run_id, lease_seconds=600 if engine == "deepagents" else 120)
    payload = claimed["payload"]
    if engine == "portfolio":
        from server.agent.synthetic_portfolio import PortfolioRequest

        options = payload["portfolio"]
        profile = PortfolioRequest(
            options["province"],
            options["score"],
            options["subject_type"],
            options["year"],
            tuple((item["school"], item["major"]) for item in options["candidates"]),
        )
    else:
        profile = DecisionCardProfile(**payload["profile"])
    try:
        store.assert_active_lease(run_id, token)
        if engine == "portfolio":
            from server.agent.synthetic_portfolio import build_synthetic_portfolio

            card = build_synthetic_portfolio(profile)
        elif engine == "deepagents":
            if agent_runner is None:
                from server.agent.deepagents_adapter import run_deepagents_research

                # Lease is checked before every SQL tool and durable receipt
                # write, not only after the model eventually returns.
                card = run_deepagents_research(
                    profile,
                    model,
                    task_id=run_id,
                    lease_guard=lambda: store.assert_active_lease(run_id, token),
                )
            else:
                card = agent_runner(profile, model, task_id=run_id)
        else:
            card = build_synthetic_decision_card(profile)
        # A cancellation during a blocking model/tool call must suppress any
        # later SQL verification and result publication. This is cooperative,
        # not a network request or OS process hard interrupt.
        store.assert_active_lease(run_id, token)
        # Do not trust the runner's self-reported status or source labels.
        # Re-read the approved fictional profile against SQL outside the Agent.
        if engine == "portfolio":
            from server.agent.synthetic_portfolio import verify_synthetic_portfolio

            if not verify_synthetic_portfolio(profile, card):
                raise ValueError("Portfolio SQL receipt does not match approved evidence")
            from server.agent.portfolio_sql_oracle import verify_portfolio_sql_truth

            verify_portfolio_sql_truth(profile, card)
            status = "SUCCEEDED" if card["eligible_history_count"] > 0 else "NEEDS_REVIEW"
        else:
            status = verify_taskrun_receipt(profile, card)
        store.assert_active_lease(run_id, token)
        failure = None if status == "SUCCEEDED" else "insufficient_verified_evidence"
        return store.finish(run_id, token, status, card, failure)
    except TaskRunConflict:
        current = store.get(run_id)
        if current["status"] == "CANCELLED":
            return current
        raise
    except Exception:  # noqa: BLE001 - never persist model/SQL/stack trace errors
        try:
            return store.finish(run_id, token, "FAILED", None, "agent_execution_failed")
        except TaskRunConflict:
            current = store.get(run_id)
            if current["status"] == "CANCELLED":
                return current
            raise


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
