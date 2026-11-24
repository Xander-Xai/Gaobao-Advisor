"""Explicitly opt-in paid-model probe for the read-only synthetic Harness.

Never invoked by CI. The provider receives fictional task text and synthetic
tool outputs only. To spend on a real provider, supply --permit-external-model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.groundtruth_pipeline import validate_reviewed_export
from benchmarks.harness_answer_eval import grade_structured_facts
from benchmarks.readonly_harness_eval import _capturing_tools, load_suite
from server.agent.readonly_harness import HarnessLimits, run_readonly_agent
from server.agent.readonly_model_planner import OpenAIJsonPlanner
from server.agent.readonly_tools import synthetic_readonly_tools


def run_model_cases(
    cases: list[dict[str, Any]],
    planner: Any,
    *,
    model_id: str = "unspecified",
    dataset_sha256: str = "",
    groundtruth_review: str = "UNREVIEWED",
    max_total_tokens: int = 12000,
) -> dict[str, Any]:
    """Score tool trajectory against manually declared synthetic task requirements."""
    if type(max_total_tokens) is not int or not 1 <= max_total_tokens <= 180000:
        raise ValueError("max_total_tokens must be 1..180000")
    reports = []
    total = 0
    stop_reason = None
    tools = synthetic_readonly_tools()
    for case in cases:
        expected = case.get("expect") or {
            "tool_sequence": [fact["tool"] for fact in case.get("expected_facts", [])],
            "min_evidence": len(case.get("expected_facts", [])),
        }
        captured: list[dict[str, Any]] = []
        result = run_readonly_agent(
            case["task"],
            planner,
            _capturing_tools(tools, captured),
            HarnessLimits(
                max_model_steps=8,
                max_tool_calls=5,
                max_reported_tokens=min(6000, max_total_tokens - total),
                require_provider_usage=True,
            ),
        )
        # Path membership is a loose signal. The answer is scored separately
        # against facts frozen before the model run and bound tool provenance.
        goal_path = set(expected["tool_sequence"]).issubset(set(result.tool_sequence))
        passed = result.status == "completed" and len(result.evidence_ids) >= expected["min_evidence"] and goal_path
        facts = grade_structured_facts(result.answer, case.get("expected_facts", []), captured)
        verified_task_passed = passed and facts["status"] == "SCORED" and facts["passed"] is True
        reports.append(
            {
                "case_id": case["id"],
                "split": case.get("split", "unassigned"),
                "goal_path_passed": passed,
                "verified_task_passed": verified_task_passed,
                "answer_facts": facts,
                "observed": result.public_report(),
            }
        )
        if result.prompt_tokens is None or result.completion_tokens is None:
            stop_reason = "provider_usage_unknown"
            break
        total += result.prompt_tokens + result.completion_tokens
        if total >= max_total_tokens:
            stop_reason = "suite_reported_token_budget_exhausted"
            break
    known = bool(reports) and all(
        item["observed"]["prompt_tokens"] is not None and item["observed"]["completion_tokens"] is not None
        for item in reports
    )
    scored = [r for r in reports if r["answer_facts"]["status"] == "SCORED"]
    return {
        "suite": "synthetic_model_driven_tool_trajectory",
        "controller": "INJECTED_PLANNER_NOT_ATTESTED",
        "model_id": model_id,
        "dataset_sha256": dataset_sha256,
        "groundtruth_review": groundtruth_review,
        "cases": len(reports),
        "planned_cases": len(cases),
        "unexecuted_case_ids": [case["id"] for case in cases[len(reports) :]],
        "suite_complete": bool(cases) and len(reports) == len(cases) and known and total <= max_total_tokens,
        "stop_reason": stop_reason,
        "max_total_reported_tokens": max_total_tokens,
        "budget_boundary": "post-response accounting; not a hard provider billing cap",
        "paired_cases": [
            {
                "case_id": row["case_id"],
                "split": row["split"],
                "fact_passed": row["answer_facts"]["passed"],
                "verified_task_passed": row["verified_task_passed"],
                "tool_calls": row["observed"]["tool_calls"],
                "total_tokens": (
                    row["observed"]["prompt_tokens"] + row["observed"]["completion_tokens"]
                    if row["observed"]["prompt_tokens"] is not None and row["observed"]["completion_tokens"] is not None
                    else None
                ),
                "safety_violation": row["observed"]["reason"]
                in {
                    # Rejected policy attempts count against candidate safety,
                    # not as successful unauthorized executions.
                    "tool_not_allowlisted",
                    "tool_schema_invalid",
                    "unverified_finish",
                    "tool_provenance_invalid",
                    "tool_evidence_invalid",
                },
            }
            for row in reports
        ],
        "failure_reasons": dict(
            sorted(Counter(row["observed"]["reason"] for row in reports if not row["verified_task_passed"]).items())
        ),
        "goal_path_passed": sum(item["goal_path_passed"] for item in reports),
        "verified_task_passed": sum(item["verified_task_passed"] for item in reports),
        "verified_task_pass_rate": (
            round(sum(item["verified_task_passed"] for item in reports) / len(reports), 4) if reports else None
        ),
        "answer_fact_scored_cases": len(scored),
        "answer_fact_passed": sum(r["answer_facts"]["passed"] is True for r in scored),
        "answer_fact_pass_rate": (
            round(sum(r["answer_facts"]["passed"] is True for r in scored) / len(scored), 4) if scored else None
        ),
        "prompt_tokens": sum(item["observed"]["prompt_tokens"] or 0 for item in reports) if known else None,
        "completion_tokens": sum(item["observed"]["completion_tokens"] or 0 for item in reports) if known else None,
        "unstructured_or_unreviewed_answer_quality": "NOT_MEASURED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "results": reports,
    }


def build_eval_plan(
    reviewed: dict[str, Any] | None, max_cases: int | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the frozen suite before importing or calling any provider."""
    if max_cases is not None and (type(max_cases) is not int or not 1 <= max_cases <= 30):
        raise ValueError("max_cases must be 1..30")
    if reviewed is not None:
        validate_reviewed_export(reviewed)
        # The review export may have a different order; keep selection deterministic.
        population = sorted(reviewed["cases"], key=lambda case: case["id"])
        cases = population[:max_cases]
        review = "REVIEW_DECLARED" if len(cases) == len(population) else "REVIEW_DECLARED_SUBSET"
        dataset_sha256 = reviewed["source_sha256"]
    else:
        population = [case for case in load_suite() if case["expect"]["status"] == "completed"]
        cases = population[: max_cases or 5]
        review = "UNREVIEWED"
        dataset_sha256 = hashlib.sha256(
            json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    if not 1 <= len(cases) <= 30:
        raise ValueError("selected case population must be 1..30")
    holdout = sum(case.get("split") == "holdout" for case in cases)
    return cases, {
        "dataset_sha256": dataset_sha256,
        "groundtruth_review": review,
        "population_cases": len(population),
        "planned_cases": len(cases),
        "planned_case_ids": [case["id"] for case in cases],
        "holdout_cases": holdout,
        "optimizer_input_eligible_if_complete": review == "REVIEW_DECLARED" and holdout >= 5,
        "selection_sha256": hashlib.sha256(
            json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-cases", type=int, help="Default: all reviewed cases, or five exploratory cases")
    parser.add_argument("--max-total-tokens", type=int, default=12000)
    parser.add_argument("--reviewed-dataset", type=Path, help="Explicit groundtruth review declaration JSON")
    parser.add_argument("--skill-policy", choices=("baseline", "staged-v1"), default="baseline")
    parser.add_argument("--plan-only", action="store_true", help="Write a frozen no-network evaluation plan")
    parser.add_argument("--permit-external-model", action="store_true")
    args = parser.parse_args(argv)
    if not args.plan_only and not args.permit_external_model:
        parser.error("Refusing paid provider calls without --permit-external-model")
    if not 1 <= args.max_total_tokens <= 180000:
        parser.error("--max-total-tokens must be 1..180000")
    if args.out.exists() or args.out.is_symlink():
        parser.error("Output must be a new evidence file")
    try:
        reviewed = json.loads(args.reviewed_dataset.read_text(encoding="utf-8")) if args.reviewed_dataset else None
        cases, plan = build_eval_plan(reviewed, args.max_cases)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    plan.update(
        model_id=args.model,
        skill_policy=args.skill_policy,
        max_total_reported_tokens=args.max_total_tokens,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.plan_only:
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump({**plan, "controller": "NOT_EXECUTED", "provider_called": False}, handle, indent=2)
            handle.write("\n")
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY required for explicit paid model probe")
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_API_BASE") or None,
        timeout=20.0,
        max_retries=0,
    )
    planner = OpenAIJsonPlanner(client, args.model, skill_policy=args.skill_policy)
    groundtruth_review = plan["groundtruth_review"]
    dataset_sha256 = plan["dataset_sha256"]
    with tempfile.TemporaryDirectory(prefix="gaobao-live-agent-eval-") as directory:
        target = Path(directory) / "synthetic.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{target}"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        os.environ["ENABLE_RAG_KB"] = "true"
        os.environ.pop("RAG_EMBEDDING_PROVIDER", None)
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=target, reset=True)
        report = run_model_cases(
            cases,
            planner,
            model_id=args.model,
            dataset_sha256=dataset_sha256,
            groundtruth_review=groundtruth_review,
            max_total_tokens=args.max_total_tokens,
        )
        from db.database import engine

        engine.dispose()
    report["controller"] = "REAL_MODEL_NOT_SCRIPTED"
    report["run_id"] = "readonly_model_" + secrets.token_hex(12)
    report["model_identity"] = planner.model_identity_receipt()
    report["skill_policy"] = planner.skill_policy_receipt()
    report["evaluation_plan"] = plan
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"model_verified_task_passed={report['verified_task_passed']}/{report['cases']} "
        f"(tool_path={report['goal_path_passed']}/{report['cases']})"
    )
    return (
        0
        if report["suite_complete"]
        and report["cases"]
        and report["verified_task_passed"] == report["cases"]
        and report["model_identity"]["provider_model_identity_consistent"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
