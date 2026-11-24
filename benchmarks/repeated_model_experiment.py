"""Repeatable, opt-in, paired *real-model* experiments on fictional SQL evidence.

Without --execute, this module ONLY prints a dry-run cost/step exposure plan.
Raw prompts, API credentials, and provider response text are not stored here.
Reported usage is provider-declared, not independently reconciled billing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any

from benchmarks.model_run_comparison import _validate, _validate_budget, compare_model_runs

ARMS = ("baseline", "evidence_first")
CASES_PER_RUN = 8
MAX_DECISIONS_PER_CASE = 8
DEFAULT_ARM_CALLS = 64
DEFAULT_ARM_TOKENS = 120_000


def compare_repeated_trials(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate *declared* full-suite runs, rejecting changed contracts or reused runs.

    Metrics are descriptive: even repeated eight-case synthetic holdouts are
    not statistical evidence or a claim about real admissions quality.
    """
    if not isinstance(pairs, list) or not 2 <= len(pairs) <= 5:
        raise ValueError("Repeated A/B comparison requires 2..5 complete pairs")
    seen_runs: set[str] = set()
    common: tuple[str, str, str, int | float] | None = None
    common_budget: tuple[int, int] | None = None
    prompt_sha: dict[str, str] = {}
    details: list[dict[str, Any]] = []
    regressions: list[str] = []
    token_deltas: list[int] = []
    old_pass = new_pass = old_tokens = new_tokens = old_tools = new_tools = 0

    for trial_index, pair in enumerate(pairs, 1):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each trial must contain baseline and evidence-first reports")
        baseline, candidate = pair
        _validate(baseline)
        _validate(candidate)
        budget = _validate_budget(baseline)
        if budget != _validate_budget(candidate):
            raise ValueError("Provider budget differs between arms")
        if trial_index == 1:
            common_budget = budget
        elif budget != common_budget:
            raise ValueError("Provider budget drift across trials")
        if baseline.get("prompt_variant") != ARMS[0] or candidate.get("prompt_variant") != ARMS[1]:
            raise ValueError("Both frozen experimental prompt arms are required")
        if baseline["model_prompt_sha256"] == candidate["model_prompt_sha256"]:
            raise ValueError("Experiment arms must actually differ in prompt")
        identity = (
            baseline["model_id"],
            baseline["dataset_sha256"],
            baseline["tool_contract_sha256"],
            baseline["temperature"],
        )
        if common is None:
            common = identity
        if identity != common:
            raise ValueError("Model/data/tool/temperature drift across trials")
        for arm, report in zip(ARMS, (baseline, candidate), strict=True):
            if report["run_id"] in seen_runs:
                raise ValueError("A benchmark run cannot be reused across trials")
            seen_runs.add(report["run_id"])
            digest = report["model_prompt_sha256"]
            if arm in prompt_sha and prompt_sha[arm] != digest:
                raise ValueError("Prompt changed within the same experiment arm")
            prompt_sha[arm] = digest
        comparison = compare_model_runs(baseline, candidate)
        holdout = comparison["groups"]["holdout"]
        regressions.extend(f"trial-{trial_index}:{key}" for key in holdout["regressed_case_ids"])
        old_pass += holdout["baseline_verified_outcomes"]
        new_pass += holdout["candidate_verified_outcomes"]
        old_tokens += holdout["baseline_provider_tokens"]
        new_tokens += holdout["candidate_provider_tokens"]
        old_tools += holdout["baseline_tool_calls"]
        new_tools += holdout["candidate_tool_calls"]
        token_deltas.append(holdout["provider_token_delta"])
        details.append(
            {
                "trial": trial_index,
                "baseline_run_id": baseline["run_id"],
                "candidate_run_id": candidate["run_id"],
                "holdout": holdout,
                "holdout_quality_non_regression": comparison["holdout_quality_non_regression"],
            }
        )

    # Do not reward fewer tokens when the candidate failed previously passing work.
    no_quality_regression = not regressions and new_pass >= old_pass
    every_trial_token_reduction = all(delta < 0 for delta in token_deltas)
    return {
        "kind": "REPEATED_DECLARED_MODEL_SYNTHETIC_COMPARISON",
        "model_id": common[0],
        "dataset_sha256": common[1],
        "tool_contract_sha256": common[2],
        "temperature": common[3],
        "provider_budget_per_arm": list(common_budget) if common_budget is not None else None,
        "prompt_sha256_by_arm": prompt_sha,
        "repeated_trials": True,
        "trial_count": len(pairs),
        "holdout_observations_per_arm": len(pairs) * 4,
        "baseline_holdout_verified_outcomes": old_pass,
        "candidate_holdout_verified_outcomes": new_pass,
        "baseline_holdout_provider_tokens": old_tokens,
        "candidate_holdout_provider_tokens": new_tokens,
        "holdout_provider_token_delta": new_tokens - old_tokens,
        "median_pair_token_delta": median(token_deltas),
        "baseline_holdout_tool_calls": old_tools,
        "candidate_holdout_tool_calls": new_tools,
        "regressions": regressions,
        "holdout_quality_non_regression": no_quality_regression,
        "every_trial_token_reduction": every_trial_token_reduction,
        "descriptive_candidate": "REVIEW_REQUIRED"
        if no_quality_regression and every_trial_token_reduction
        else "REJECT_CANDIDATE",
        "pairs": details,
        "human_review_complete": False,
        "provenance_independently_verified": False,
        "provider_billing_reconciled": False,
        "provider_cost_usd": None,
        "statistical_significance": "NOT_ESTABLISHED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "production_deployment_authorized": False,
        "warning": "Self-reported model traces, tiny fictional holdouts and unreconciled usage cannot authorize production.",
    }


def _plan(
    trials: int, model: str, max_calls: int = DEFAULT_ARM_CALLS, stop_tokens: int = DEFAULT_ARM_TOKENS
) -> dict[str, Any]:
    return {
        "model_id": model,
        "trial_count": trials,
        "arms": list(ARMS),
        "cases_per_run": CASES_PER_RUN,
        "total_synthetic_case_executions": trials * len(ARMS) * CASES_PER_RUN,
        "maximum_possible_model_decisions": trials * len(ARMS) * CASES_PER_RUN * MAX_DECISIONS_PER_CASE,
        "provider_request_cap_per_arm": max_calls,
        "provider_reported_token_stop_per_arm": stop_tokens,
        "aggregate_request_cap": trials * len(ARMS) * max_calls,
        "sum_of_reported_token_stop_thresholds": trials * len(ARMS) * stop_tokens,
        "hard_token_cap": False,
        "provider_usd_upper_bound": "UNKNOWN",
        "requires_explicit_paid_provider_consent": True,
        "real_admissions_accuracy": "NOT_MEASURED",
    }


def _run_arm(
    model: str,
    arm: str,
    output: Path,
    timeout_seconds: int,
    *,
    max_calls: int = DEFAULT_ARM_CALLS,
    stop_tokens: int = DEFAULT_ARM_TOKENS,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "benchmarks.decision_card_business_eval",
        "--mode",
        "model",
        "--model",
        model,
        "--prompt-variant",
        arm,
        "--permit-external-model",
        "--max-cases",
        str(CASES_PER_RUN),
        "--max-provider-calls",
        str(max_calls),
        "--stop-after-reported-tokens",
        str(stop_tokens),
        "--out",
        str(output),
    ]
    # Code 1 means at least one model-directed task failed its business oracle,
    # not necessarily an infrastructure failure. Validate report contents either way.
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_seconds)
    if completed.returncode not in {0, 1} or not output.is_file():
        raise RuntimeError("Model trial failed before producing a complete report")
    report = json.loads(output.read_text(encoding="utf-8"))
    _validate(report)
    if report.get("model_id") != model or report.get("prompt_variant") != arm:
        raise ValueError("Unexpected model or experimental prompt arm in report")
    return report


def _validate_paid_prefix(report: dict[str, Any], prior: list[dict[str, Any]], model: str, arm: str) -> None:
    """Reject incompatible completed arms before spending on any further trial."""
    indexed = _validate(report)
    if report["model_id"] != model or report.get("prompt_variant") != arm:
        raise ValueError("Unexpected model or experimental prompt arm in report")
    fields = ("dataset_sha256", "tool_contract_sha256", "temperature")
    for previous in prior:
        if report["run_id"] == previous["run_id"]:
            raise ValueError("Repeated paid run identity")
        if any(report[field] != previous[field] for field in fields):
            raise ValueError("Frozen experiment contract drift")
        previous_cases = {row["case_id"]: row["split"] for row in previous["results"]}
        if {key: row["split"] for key, row in indexed.items()} != previous_cases:
            raise ValueError("Frozen experiment case partition drift")
        same_arm = previous["prompt_variant"] == arm
        same_prompt = previous["model_prompt_sha256"] == report["model_prompt_sha256"]
        if same_arm != same_prompt:
            raise ValueError("Frozen experiment prompt drift")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Explicit provider model ID; avoid drifting aliases")
    parser.add_argument("--trials", type=int, default=2, help="2..5 paired repeated runs")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--max-provider-calls-per-arm", type=int, default=DEFAULT_ARM_CALLS)
    parser.add_argument("--stop-after-reported-tokens-per-arm", type=int, default=DEFAULT_ARM_TOKENS)
    parser.add_argument("--execute", action="store_true", help="Actually call a paid external model")
    parser.add_argument("--permit-external-model", action="store_true")
    parser.add_argument("--confirm-provider-charges", action="store_true")
    args = parser.parse_args(argv)
    if not isinstance(args.model, str) or not args.model.strip() or len(args.model) > 160:
        parser.error("Explicit nonempty model ID is required")
    if not 2 <= args.trials <= 5 or not 60 <= args.timeout_seconds <= 3600:
        parser.error("Trials must be 2..5 and timeout 60..3600 seconds")
    if not 1 <= args.max_provider_calls_per_arm <= 128 or not 1 <= args.stop_after_reported_tokens_per_arm <= 500_000:
        parser.error("Per-arm provider budget out of range")
    budget = (args.max_provider_calls_per_arm, args.stop_after_reported_tokens_per_arm)
    plan = _plan(args.trials, args.model, *budget)
    if not args.execute:
        if args.permit_external_model or args.confirm_provider_charges:
            parser.error("Do not authorize provider charges in dry-run mode")
        print(json.dumps({"mode": "DRY_RUN_NO_NETWORK", **plan}, ensure_ascii=False, indent=2))
        return 0
    if not (args.permit_external_model and args.confirm_provider_charges):
        parser.error("Execution requires --permit-external-model AND --confirm-provider-charges")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required for explicitly authorized model execution")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        parser.error("Output directory must be empty to prevent report overwrite or accidental reuse")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    completed_reports: list[dict[str, Any]] = []
    for i in range(1, args.trials + 1):
        reports = {}
        # Alternate call order to reduce fixed API warmup/order bias.
        order = ARMS if i % 2 else tuple(reversed(ARMS))
        for arm in order:
            path = args.out_dir / f"trial-{i}-{arm}.json"
            reports[arm] = _run_arm(
                args.model, arm, path, args.timeout_seconds, max_calls=budget[0], stop_tokens=budget[1]
            )
            if _validate_budget(reports[arm]) != budget:
                raise ValueError("Executed arm lacks the requested reconciled provider budget; stop experiment")
            _validate_paid_prefix(reports[arm], completed_reports, args.model, arm)
            completed_reports.append(reports[arm])
        pairs.append((reports[ARMS[0]], reports[ARMS[1]]))
    result = compare_repeated_trials(pairs)
    result["execution_plan"] = plan
    output = args.out_dir / "repeated-experiment-summary.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        result["descriptive_candidate"]
        + " (real-model run declarations; independent billing/human review still required)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
