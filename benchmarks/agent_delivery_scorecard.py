"""Evidence-bound synthetic Agent delivery scorecard.

This is *not* a real admissions north-star. Only provider-declared usage and
fixture/SQL-checked synthetic outcomes can be measured here. Independently
reviewed expert GT, production delivery and actual bills stay NOT_MEASURED.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.decision_card_trace_eval import check_trace_receipt
from benchmarks.model_run_comparison import _validate as validate_model_report


def _nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000000


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(0.95 * len(ordered)) - 1], 2)


def summarize_synthetic_delivery(report: dict[str, Any]) -> dict[str, Any]:
    """Reject incomplete/tampered reports; return only scalar safe statistics."""
    if not isinstance(report, dict) or report.get("dataset_profile") != "SYNTHETIC_DEMO":
        raise ValueError("Only synthetic admissions evaluation reports are supported")
    controller = report.get("controller")
    if controller == "REAL_MODEL_NOT_SCRIPTED":
        validate_model_report(report)
    elif controller != "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM":
        raise ValueError("Untrusted or unsupported execution controller")
    rows = report.get("results")
    count = report.get("cases")
    if not isinstance(rows, list) or type(count) is not int or not 1 <= count <= 8 or len(rows) != count:
        raise ValueError("Expected bounded complete case collection")
    ids: set[str] = set()
    passed = 0
    tool_calls = model_steps = 0
    elapsed: list[float] = []
    failure_codes: Counter[str] = Counter()
    tokens_known = controller == "REAL_MODEL_NOT_SCRIPTED"
    prompt_tokens = completion_tokens = 0
    attempts = successes = 0
    has_verified_trace = True
    allowed_failure_codes = {
        "factual_mismatch", "source_contract_invalid", "incomplete_evidence",
        "outcome_mismatch", "negative_evidence_not_observed", "unsafe_partial_decision",
        "runtime_failure_not_valid_abstention", "wrong_data_profile", "school_sources_invalid",
        "trace_contract_invalid",
    }
    for row in rows:
        case_id = row.get("case_id") if isinstance(row, dict) else None
        if (
            not isinstance(case_id, str) or not case_id or len(case_id) > 100
            or case_id in ids or type(row.get("passed")) is not bool
            or type(row.get("tool_calls")) is not int or not 0 <= row["tool_calls"] <= 5
            or type(row.get("model_steps")) is not int or not 0 <= row["model_steps"] <= 8
            or not _nonnegative(row.get("elapsed_ms"))
        ):
            raise ValueError("Malformed case identity, result or execution counters")
        ids.add(case_id)
        errors = row.get("failure_codes")
        if not isinstance(errors, list) or len(errors) > 12 or any(
            not isinstance(item, str) or item not in allowed_failure_codes for item in errors
        ):
            raise ValueError("Unrecognized result failure codes")
        failure_codes.update(errors)
        passed += int(row["passed"])
        tool_calls += row["tool_calls"]
        model_steps += row["model_steps"]
        elapsed.append(row["elapsed_ms"])
        p, c = row.get("prompt_tokens"), row.get("completion_tokens")
        if tokens_known and (type(p) is not int or type(c) is not int or p < 0 or c < 0):
            raise ValueError("Model usage is required and cannot be estimated")
        if type(p) is int and type(c) is int and p >= 0 and c >= 0:
            prompt_tokens += p
            completion_tokens += c
        else:
            tokens_known = False
        receipt = row.get("trace_receipt")
        if receipt is None:
            has_verified_trace = False
        else:
            check_trace_receipt(row)
            for event in receipt["step_events"]:
                if event["kind"] == "tool" and event["result"] in {
                    "ok", "tool_execution_failed", "tool_provenance_invalid",
                    "tool_evidence_invalid", "context_budget_exhausted",
                }:
                    attempts += 1
                    successes += int(event["result"] == "ok")
    if passed != report.get("passed") or tool_calls != report.get("total_tool_calls", tool_calls):
        raise ValueError("Case counts disagree with frozen evaluation summary")
    if tokens_known and (
        prompt_tokens != report.get("provider_prompt_tokens")
        or completion_tokens != report.get("provider_completion_tokens")
    ):
        raise ValueError("Usage totals do not reconcile")
    if has_verified_trace and attempts != tool_calls:
        raise ValueError("Redacted tool steps do not reconcile with execution totals")
    return {
        "kind": "SYNTHETIC_AGENT_DELIVERY_SCORECARD_V1",
        "data_profile": "SYNTHETIC_DEMO",
        "controller": controller,
        "synthetic_case_count": count,
        "synthetic_verified_case_count": passed,
        "synthetic_task_pass_rate": round(passed / count, 4),
        "p95_synthetic_elapsed_ms": _percentile_95(elapsed),
        "average_tool_calls_per_passing_case": round(tool_calls / passed, 4) if passed else None,
        "average_model_decisions_per_passing_case": round(model_steps / passed, 4) if passed else None,
        "tool_contract_success_rate": round(successes / attempts, 4)
        if has_verified_trace and attempts else None,
        "total_provider_declared_tokens": prompt_tokens + completion_tokens if tokens_known else None,
        "provider_declared_tokens_per_passing_case": round((prompt_tokens + completion_tokens) / passed, 2)
        if tokens_known and passed else None,
        "failure_count_by_code": dict(sorted(failure_codes.items())),
        "trace_complete": has_verified_trace,
        "independent_expert_ground_truth": False,
        "real_user_effective_delivery_rate": "NOT_MEASURED",
        "provider_billing_verified": False,
        "actual_cost_per_delivery": None,
        "production_release_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.run.stat().st_size > 2_000_000:
        parser.error("Evaluation report exceeds bounded size")
    report = json.loads(args.run.read_text(encoding="utf-8"))
    summary = summarize_synthetic_delivery(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SYNTHETIC_DELIVERY_SCORECARD (not real admissions efficacy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
