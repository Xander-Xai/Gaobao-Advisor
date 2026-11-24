"""Offline comparison of two *declared* model-directed synthetic business runs.

Neither report is trusted merely because it says REAL_MODEL_NOT_SCRIPTED.
This never calls providers, estimates prices, endorses release, or certifies
human review. For a benchmark, execute the existing opt-in model mode twice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.decision_card_trace_eval import TRACE_CONTRACT, check_trace_receipt

_CONTRACT = "SYNTHETIC_DECISION_CARD_V1"


def _validate_budget(report: dict[str, Any]) -> tuple[int, int] | None:
    """Reconcile a new-style budget receipt; legacy reports remain descriptive only."""
    if "provider_budget" not in report and "provider_usage_reconciled" not in report:
        return None
    budget = report.get("provider_budget")
    if not isinstance(budget, dict) or report.get("provider_usage_reconciled") is not True:
        raise ValueError("Missing reconciled provider budget receipt")
    fields = (
        "max_calls",
        "stop_after_reported_tokens",
        "attempts",
        "known_usage_responses",
        "known_prompt_tokens",
        "known_completion_tokens",
    )
    if any(type(budget.get(field)) is not int or budget[field] < 0 for field in fields):
        raise ValueError("Invalid provider budget counters")
    calls, threshold = budget["max_calls"], budget["stop_after_reported_tokens"]
    if not 1 <= calls <= 128 or not 1 <= threshold <= 500_000:
        raise ValueError("Invalid provider budget configuration")
    if (
        not 0 < budget["attempts"] < calls
        or budget["attempts"] != budget["known_usage_responses"]
        or budget["known_prompt_tokens"] != report.get("provider_prompt_tokens")
        or budget["known_completion_tokens"] != report.get("provider_completion_tokens")
        or budget["known_prompt_tokens"] + budget["known_completion_tokens"] >= threshold
        or budget.get("usage_complete") is not True
        or budget.get("stop_reason") is not None
        or budget.get("hard_token_cap") is not False
        or report.get("requested_cases") != 8
        or report.get("unattempted_case_ids") != []
    ):
        raise ValueError("Provider budget receipt is incomplete or inconsistent")
    return calls, threshold


def _validate(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        raise ValueError("Invalid report")
    if report.get("controller") != "REAL_MODEL_NOT_SCRIPTED":
        raise ValueError("Scripted run is not a model A/B measurement")
    if report.get("evaluation_contract") != _CONTRACT or report.get("dataset_profile") != "SYNTHETIC_DEMO":
        raise ValueError("Unsupported business evaluation contract")
    if report.get("human_review_complete") is not False:
        raise ValueError("Only explicitly unreviewed synthetic fixtures are supported")
    if report.get("comparison_eligible") is not True:
        raise ValueError("Incomplete or non-model benchmark run")
    if not isinstance(report.get("run_id"), str) or not report["run_id"].startswith("synthetic_eval_"):
        raise ValueError("Missing run identity")
    if not isinstance(report.get("model_id"), str) or not report["model_id"]:
        raise ValueError("Missing model identity")
    if type(report.get("temperature")) not in (int, float) or report["temperature"] != 0:
        raise ValueError("Unexpected generation temperature")
    for field in ("dataset_sha256", "model_prompt_sha256", "tool_contract_sha256"):
        value = report.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("Missing frozen run or tool digest")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError("Malformed run digest") from exc
    if report.get("provider_token_source") != "OPENAI_COMPATIBLE_REPORTED_USAGE":
        raise ValueError("Provider token accounting is not declared")
    cases = report.get("results")
    if not isinstance(cases, list) or len(cases) != 8 or report.get("cases") != 8:
        raise ValueError("Full eight-case suite required")
    indexed: dict[str, dict[str, Any]] = {}
    for row in cases:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ValueError("Malformed case")
        if row["case_id"] in indexed or row.get("split") not in {"development", "holdout"}:
            raise ValueError("Duplicate case or missing split")
        if type(row.get("passed")) is not bool or type(row.get("safe_abstain")) is not bool:
            raise ValueError("Missing independently scored case outcome")
        for field in ("prompt_tokens", "completion_tokens", "tool_calls"):
            value = row.get(field)
            if type(value) is not int or value < 0:
                raise ValueError("Missing provider usage or actual tool calls")
        latency = row.get("elapsed_ms")
        if type(latency) not in (int, float) or not 0 <= latency <= 1_000_000:
            raise ValueError("Missing valid elapsed duration")
        if not isinstance(row.get("failure_codes"), list):
            raise ValueError("Missing failure categories")
        indexed[row["case_id"]] = row
    # New trace-bearing reports require every case to carry a regradable receipt.
    # Legacy no-trace declared reports are still accepted but have no trace evidence.
    contract = report.get("trace_contract")
    if contract is not None:
        if contract != TRACE_CONTRACT or report.get("trace_validated_cases") != 8:
            raise ValueError("Invalid redacted trace contract coverage")
        for row in cases:
            check_trace_receipt(row)
    elif any("trace_receipt" in row or "execution_status" in row for row in cases):
        raise ValueError("Trace receipt is present without its versioned contract")
    if sum(row["split"] == "holdout" for row in cases) != 4:
        raise ValueError("Expected frozen four-case holdout")
    if sum(row["prompt_tokens"] for row in cases) != report.get("provider_prompt_tokens"):
        raise ValueError("Inconsistent prompt token total")
    if sum(row["completion_tokens"] for row in cases) != report.get("provider_completion_tokens"):
        raise ValueError("Inconsistent completion token total")
    if sum(row["passed"] for row in cases) != report.get("passed"):
        raise ValueError("Inconsistent passed-case count")
    _validate_budget(report)
    return indexed


def compare_model_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Paired descriptive quality/efficiency evidence, NOT a release decision."""
    old = _validate(baseline)
    new = _validate(candidate)
    old_budget, new_budget = _validate_budget(baseline), _validate_budget(candidate)
    if old_budget != new_budget:
        raise ValueError("Provider budget contracts differ across comparison arms")
    if baseline["run_id"] == candidate["run_id"]:
        raise ValueError("A run cannot be paired with itself")
    for field in ("model_id", "dataset_sha256", "tool_contract_sha256", "temperature"):
        if baseline[field] != candidate[field]:
            raise ValueError("Model, fixture, tool or temperature changed")
    if set(old) != set(new) or any(old[key]["split"] != new[key]["split"] for key in old):
        raise ValueError("Case identities or development/holdout partitions changed")
    grouped = {}
    for split in ("development", "holdout"):
        keys = sorted(key for key in old if old[key]["split"] == split)
        old_pass = sum(old[key]["passed"] for key in keys)
        new_pass = sum(new[key]["passed"] for key in keys)
        old_tools = sum(old[key]["tool_calls"] for key in keys)
        new_tools = sum(new[key]["tool_calls"] for key in keys)
        old_tokens = sum(old[key]["prompt_tokens"] + old[key]["completion_tokens"] for key in keys)
        new_tokens = sum(new[key]["prompt_tokens"] + new[key]["completion_tokens"] for key in keys)
        grouped[split] = {
            "cases": len(keys),
            "baseline_verified_outcomes": old_pass,
            "candidate_verified_outcomes": new_pass,
            "baseline_safe_abstentions": sum(old[key]["safe_abstain"] for key in keys),
            "candidate_safe_abstentions": sum(new[key]["safe_abstain"] for key in keys),
            "baseline_tool_calls": old_tools,
            "candidate_tool_calls": new_tools,
            "tool_call_delta": new_tools - old_tools,
            "baseline_provider_tokens": old_tokens,
            "candidate_provider_tokens": new_tokens,
            "provider_token_delta": new_tokens - old_tokens,
            "baseline_elapsed_ms_total": sum(old[key]["elapsed_ms"] for key in keys),
            "candidate_elapsed_ms_total": sum(new[key]["elapsed_ms"] for key in keys),
            "regressed_case_ids": [key for key in keys if old[key]["passed"] and not new[key]["passed"]],
        }
    holdout = grouped["holdout"]
    non_regression = (
        holdout["candidate_verified_outcomes"] >= holdout["baseline_verified_outcomes"]
        and not holdout["regressed_case_ids"]
    )
    cheaper = holdout["provider_token_delta"] < 0 or (
        holdout["provider_token_delta"] == 0 and holdout["tool_call_delta"] < 0
    )
    return {
        "kind": "DECLARED_MODEL_PAIRED_SYNTHETIC_COMPARISON",
        "baseline_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "model_id": baseline["model_id"],
        "same_prompt": baseline["model_prompt_sha256"] == candidate["model_prompt_sha256"],
        "provider_budget_contract_checked": old_budget is not None,
        "dataset_sha256": baseline["dataset_sha256"],
        "groups": grouped,
        "holdout_quality_non_regression": non_regression,
        "holdout_token_or_tool_reduction": cheaper,
        "descriptive_candidate": "REVIEW_REQUIRED" if non_regression and cheaper else "REJECT_CANDIDATE",
        "production_deployment_authorized": False,
        "provenance_independently_verified": False,
        "human_review_complete": False,
        "provider_cost_usd": None,
        "repeated_trials": False,
        "statistical_significance": "NOT_ESTABLISHED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "warning": "Self-declared run metadata and four synthetic holdout cases cannot authorize deployment.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = compare_model_runs(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.candidate.read_text(encoding="utf-8")),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["descriptive_candidate"] + " (not deployment approval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
