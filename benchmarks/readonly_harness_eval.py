"""Thirty-case offline Harness contract suite with independent expected outcomes.

This is a scripted controller reliability test, **not model planning quality**.
Run: python -m benchmarks.readonly_harness_eval --out /tmp/readonly-agent-eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmarks.agent_eval_replay import _block_outbound_network
from server.agent.readonly_harness import Decision, ReadOnlyTool, run_readonly_agent
from server.agent.readonly_tools import synthetic_readonly_tools

_CASES_PATH = Path(__file__).parent / "fixtures" / "readonly_harness_cases.json"


class ScriptedController:
    """Deterministic plans for policy testing; never claim model autonomy."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = iter(script)

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        item = next(self._script)
        if item["kind"] == "raise":
            raise RuntimeError("deliberate offline planner failure")
        refs = item.get("evidence_ids", [])
        if item.get("use_seen_evidence"):
            refs = sorted({source for event in history for source in event.get("source_ids", [])})
        return Decision(
            kind=item["kind"],
            tool=item.get("tool", ""),
            arguments=item.get("arguments", {}),
            answer=item.get("answer", ""),
            evidence_ids=refs,
        )


def _faulted_tools(tools: dict[str, ReadOnlyTool], case: dict[str, Any]) -> dict[str, ReadOnlyTool]:
    fault = case.get("fault")
    if not fault:
        return tools
    target = fault["tool"]
    if target not in tools:
        raise ValueError("Fault target is not in the tool allowlist")
    mode = fault["mode"]
    if mode == "raise":

        def handler(_args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("deliberate synthetic tool fault")
    elif mode == "bad_provenance":

        def handler(_args: dict[str, Any]) -> dict[str, Any]:
            return {"data_profile": "CUSTOM", "source_ids": []}
    else:
        raise ValueError("Unsupported fault mode")
    updated = dict(tools)
    updated[target] = replace(updated[target], handler=handler)
    return updated


def load_suite(path: Path = _CASES_PATH) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases or len(cases) > 100:
        raise ValueError("Expected 1-100 offline cases")
    ids: set[str] = set()
    for case in cases:
        if (
            not isinstance(case, dict)
            or case.get("synthetic_demo") is not True
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("task"), str)
            or not isinstance(case.get("script"), list)
            or not isinstance(case.get("expect"), dict)
            or not case["script"]
            or case["id"] in ids
        ):
            raise ValueError("Invalid/duplicate synthetic eval case")
        ids.add(case["id"])
    return cases


def run_suite(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare actual execution with predeclared safety/workflow oracles."""
    tools = synthetic_readonly_tools()
    results: list[dict[str, Any]] = []
    for case in cases:
        result = run_readonly_agent(
            case["task"],
            ScriptedController(case["script"]),
            _faulted_tools(tools, case),
        )
        expected = case["expect"]
        passed = (
            result.status == expected["status"]
            and result.reason == expected["reason"]
            and result.tool_sequence == expected["tool_sequence"]
            and len(result.evidence_ids) >= expected["min_evidence"]
        )
        results.append(
            {
                "case_id": case["id"],
                "passed": passed,
                "expected_status": expected["status"],
                "observed": result.public_report(),
            }
        )
    n = len(results)
    completed = sum(row["observed"]["status"] == "completed" for row in results)
    counts = Counter(row["observed"]["reason"] for row in results)
    return {
        "suite": "synthetic_readonly_harness_contract",
        "controller": "SCRIPTED_NOT_LLM",
        "data_profile": "SYNTHETIC_DEMO",
        "cases": n,
        "policy_contract_passed": sum(row["passed"] for row in results),
        "policy_contract_pass_rate": round(sum(row["passed"] for row in results) / n, 4) if n else None,
        "completed_cases": completed,
        "blocked_cases": sum(row["observed"]["status"] == "blocked" for row in results),
        "mean_tool_calls": round(sum(row["observed"]["tool_calls"] for row in results) / n, 2) if n else None,
        "total_tool_calls": sum(row["observed"]["tool_calls"] for row in results),
        "failure_reasons": dict(sorted(counts.items())),
        "provider_token_usage": "NOT_MEASURED",
        "independent_model_quality": "NOT_MEASURED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "cases_detail": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=_CASES_PATH)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    cases = load_suite(args.cases)
    # Default offline execution NEVER uses a user's existing DB.
    with tempfile.TemporaryDirectory(prefix="gaobao-harness-eval-") as directory:
        target = Path(directory) / "synthetic.sqlite"
        os.environ["DATABASE_URL"] = f"sqlite:///{target}"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        os.environ["ENABLE_RAG_KB"] = "true"
        os.environ.pop("RAG_EMBEDDING_PROVIDER", None)
        with _block_outbound_network():
            from scripts.seed_demo_data import seed_database

            seed_database(db_path=target, reset=True)
            report = run_suite(cases)
            from db.database import engine

            engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"policy_contract_passed={report['policy_contract_passed']}/{report['cases']}")
    return 0 if report["policy_contract_passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
