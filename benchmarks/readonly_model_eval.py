"""Explicitly opt-in paid-model probe for the read-only synthetic Harness.

Never invoked by CI. The provider receives fictional task text and synthetic
tool outputs only. To spend on a real provider, supply --permit-external-model.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.readonly_harness_eval import load_suite
from server.agent.readonly_harness import HarnessLimits, run_readonly_agent
from server.agent.readonly_model_planner import OpenAIJsonPlanner
from server.agent.readonly_tools import synthetic_readonly_tools


def run_model_cases(cases: list[dict[str, Any]], planner: Any) -> dict[str, Any]:
    """Score tool trajectory against manually declared synthetic task requirements."""
    reports = []
    tools = synthetic_readonly_tools()
    for case in cases:
        expected = case["expect"]
        result = run_readonly_agent(
            case["task"],
            planner,
            tools,
            HarnessLimits(max_model_steps=8, max_tool_calls=5, max_reported_tokens=6000, require_provider_usage=True),
        )
        # Source IDs are generated from real synthetic tool results, not the model.
        goal_path = set(expected["tool_sequence"]).issubset(set(result.tool_sequence))
        passed = result.status == "completed" and len(result.evidence_ids) >= expected["min_evidence"] and goal_path
        reports.append({"case_id": case["id"], "goal_path_passed": passed, "observed": result.public_report()})
    known = all(item["observed"]["prompt_tokens"] is not None for item in reports)
    return {
        "suite": "synthetic_model_driven_tool_trajectory",
        "controller": "REAL_MODEL_NOT_SCRIPTED",
        "cases": len(reports),
        "goal_path_passed": sum(item["goal_path_passed"] for item in reports),
        "prompt_tokens": sum(item["observed"]["prompt_tokens"] or 0 for item in reports) if known else None,
        "completion_tokens": sum(item["observed"]["completion_tokens"] or 0 for item in reports) if known else None,
        "model_answer_semantic_quality": "NOT_MEASURED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "results": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-cases", type=int, default=5)
    parser.add_argument("--permit-external-model", action="store_true")
    args = parser.parse_args(argv)
    if not args.permit_external_model:
        parser.error("Refusing paid provider calls without --permit-external-model")
    if not 1 <= args.max_cases <= 22:
        parser.error("--max-cases must be 1..22")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY required for explicit paid model probe")
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_API_BASE") or None,
        timeout=20.0,
        max_retries=0,
    )
    planner = OpenAIJsonPlanner(client, args.model)
    # Choose positive, pre-reviewed synthetic task specs only.
    cases = [case for case in load_suite() if case["expect"]["status"] == "completed"][: args.max_cases]
    with tempfile.TemporaryDirectory(prefix="gaobao-live-agent-eval-") as directory:
        target = Path(directory) / "synthetic.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{target}"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        os.environ["ENABLE_RAG_KB"] = "true"
        os.environ.pop("RAG_EMBEDDING_PROVIDER", None)
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=target, reset=True)
        report = run_model_cases(cases, planner)
        from db.database import engine

        engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"model_tool_goal_passed={report['goal_path_passed']}/{report['cases']}")
    return 0 if report["goal_path_passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
