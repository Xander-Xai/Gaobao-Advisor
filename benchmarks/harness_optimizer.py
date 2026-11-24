"""Offline Harness optimization gate: candidate generation, paired Eval, no auto-deploy.

This only evaluates externally generated measurements. It NEVER calls a model,
changes production configuration, or treats a scripted test as model evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def propose_bounded_changes(failure_codes: list[str]) -> list[dict[str, Any]]:
    """Suggest small, reviewable hypotheses; never claim measured improvements."""
    errors = Counter(failure_codes)
    proposals: list[dict[str, Any]] = []
    if errors["tool_repeat_loop"]:
        proposals.append(
            {
                "hypothesis": "Early repetition feedback might reduce wasted tool steps",
                "configuration": {"max_repeated_call": 1},
                "state": "UNTESTED",
            }
        )
    if errors["unverified_finish"] or errors["fact_mismatch"]:
        proposals.append(
            {
                "hypothesis": "Structured evidence checklist might reduce incorrect or unsupported finish",
                "configuration": {"planner_style": "strict_evidence"},
                "state": "UNTESTED",
            }
        )
    if errors["reported_token_budget_exhausted"]:
        proposals.append(
            {
                "hypothesis": "Shorter tool observation payloads might reduce context token usage",
                "configuration": {"max_observation_chars": 400},
                "state": "UNTESTED",
            }
        )
    return proposals


def _indexed(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if result.get("controller") != "REAL_MODEL_NOT_SCRIPTED":
        raise ValueError("Paired optimization requires explicitly declared real-model runs")
    if result.get("groundtruth_review") != "REVIEW_DECLARED":
        raise ValueError("Optimization requires an explicit reviewed dataset declaration")
    if result.get("suite_complete") is not True or result.get("unexecuted_case_ids") != []:
        raise ValueError("Incomplete model evaluation suite is not eligible for optimization")
    cases = result.get("paired_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Missing paired result cases")
    indexed: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ValueError("Malformed case")
        if case["case_id"] in indexed:
            raise ValueError("Duplicate case ID")
        if case.get("split") not in {"development", "holdout"}:
            raise ValueError("Missing explicit frozen split")
        if type(case.get("fact_passed")) is not bool:
            raise ValueError("Missing independently scored factual result")
        if not isinstance(case.get("verified_task_passed"), bool):
            raise ValueError("Missing verified task completion result")
        if case["verified_task_passed"] and not case["fact_passed"]:
            raise ValueError("Inconsistent verified task result")
        if type(case.get("tool_calls")) is not int or case["tool_calls"] < 0:
            raise ValueError("Missing actual tool call count")
        if type(case.get("total_tokens")) is not int or case["total_tokens"] < 0:
            raise ValueError("Cannot recommend cost improvements without real provider usage")
        if type(case.get("safety_violation")) is not bool:
            raise ValueError("Missing safety flag")
        indexed[case["case_id"]] = case
    return indexed


def compare_holdout(baseline: dict[str, Any], candidate: dict[str, Any], min_holdout: int = 5) -> dict[str, Any]:
    """Make a *review proposal* only after paired holdout non-regression checks.

    Never generate deploy permission. Provenance is self-declared here, so CI,
    tool logs and provider billing data must be verified separately by humans.
    """
    b = _indexed(baseline)
    c = _indexed(candidate)
    if (
        baseline.get("dataset_sha256") != candidate.get("dataset_sha256")
        or not isinstance(baseline.get("dataset_sha256"), str)
        or len(baseline["dataset_sha256"]) != 64
        or baseline.get("model_id") != candidate.get("model_id")
        or not baseline.get("model_id")
        or set(b) != set(c)
    ):
        raise ValueError("Paired data/model/case mismatch")
    if any(b[key]["split"] != c[key]["split"] for key in b):
        raise ValueError("Development/holdout split changed")
    holdout = [key for key in b if b[key]["split"] == "holdout"]
    if len(holdout) < min_holdout:
        raise ValueError("Insufficient frozen holdout coverage")
    b_success = sum(b[key]["fact_passed"] for key in holdout)
    c_success = sum(c[key]["fact_passed"] for key in holdout)
    b_tokens = sum(b[key]["total_tokens"] for key in holdout)
    c_tokens = sum(c[key]["total_tokens"] for key in holdout)
    b_tools = sum(b[key]["tool_calls"] for key in holdout)
    c_tools = sum(c[key]["tool_calls"] for key in holdout)
    b_safety = sum(b[key]["safety_violation"] for key in holdout)
    c_safety = sum(c[key]["safety_violation"] for key in holdout)
    fact_regressions = sorted(key for key in holdout if b[key]["fact_passed"] and not c[key]["fact_passed"])
    task_regressions = sorted(
        key for key in holdout if b[key]["verified_task_passed"] and not c[key]["verified_task_passed"]
    )
    new_safety_violations = sorted(
        key for key in holdout if c[key]["safety_violation"] and not b[key]["safety_violation"]
    )
    non_regression = not (fact_regressions or task_regressions or new_safety_violations)
    improvement = c_tokens < b_tokens or (c_tokens == b_tokens and c_tools < b_tools)
    return {
        "decision": "REVIEW_REQUIRED" if non_regression and improvement else "REJECT_CANDIDATE",
        "production_deployment_authorized": False,
        "evidence_kind": "DECLARED_REAL_MODEL_RUNS_NOT_INDEPENDENTLY_VERIFIED",
        "holdout_cases": len(holdout),
        "baseline_fact_passed": b_success,
        "candidate_fact_passed": c_success,
        "baseline_verified_task_passed": sum(b[key]["verified_task_passed"] for key in holdout),
        "candidate_verified_task_passed": sum(c[key]["verified_task_passed"] for key in holdout),
        "fact_regression_case_ids": fact_regressions,
        "task_regression_case_ids": task_regressions,
        "new_safety_violation_case_ids": new_safety_violations,
        "baseline_safety_violations": b_safety,
        "candidate_safety_violations": c_safety,
        "baseline_total_tokens": b_tokens,
        "candidate_total_tokens": c_tokens,
        "token_delta": c_tokens - b_tokens,
        "baseline_total_tool_calls": b_tools,
        "candidate_total_tool_calls": c_tools,
        "tool_call_delta": c_tools - b_tools,
        "quality_non_regression": non_regression,
        "reproducibility": "NOT_ESTABLISHED_BY_SINGLE_COMPARISON",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    b = json.loads(args.baseline.read_text(encoding="utf-8"))
    c = json.loads(args.candidate.read_text(encoding="utf-8"))
    report = compare_holdout(b, c)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report["decision"])
    return 0 if report["decision"] == "REVIEW_REQUIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
