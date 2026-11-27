"""Paired Context Engineering experiment on the existing fictional admissions SQL oracle.

Default: dry-run with NO API calls. --scripted-smoke uses a fake OpenAI-compatible
controller and is NOT evidence of real LLM token savings or quality. Paid model
A/B requires --execute --permit-external-model --confirm-provider-charges,
a private OPENAI_API_KEY, an explicit model ID and a nonproduction environment.

Examples:
  python -m benchmarks.context_ab_eval --model YOUR_MODEL_ID
  python -m benchmarks.context_ab_eval --scripted-smoke --out-dir /tmp/context-smoke
  python -m benchmarks.context_ab_eval --execute --permit-external-model \
    --confirm-provider-charges --model YOUR_MODEL_ID --trials 2 \
    --out-dir /tmp/context-real-model
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any

from benchmarks.decision_card_business_eval import ProviderBudget, frozen_cases, run_cases
from server.agent.context_ablation import ContextABPlanner
from server.agent.decision_card_model_planner import DecisionCardModelPlanner, prompt_for_variant

ARMS = ("full", "evidence_compact")
_CONTRACT = "SYNTHETIC_CONTEXT_AB_V1"
_CASES = 8
_STEPS_UPPER_BOUND = 8
_DEFAULT_ARM_CALLS = 64
_DEFAULT_ARM_TOKENS = 120_000
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{1,159}$")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class _FakeCompletions:
    """Scripted action selector, never a model; deliberately reports fake usage."""

    def create(self, **kwargs: Any) -> Any:
        payload = json.loads(kwargs["messages"][1]["content"])
        if payload["requires_abstention_due_to_missing_data"]:
            action = {"kind": "abstain"}
        elif payload["required_remaining"]:
            action = {"kind": "tool", **payload["required_remaining"][0]}
        else:
            refs = []
            for event in payload["tool_observations_data_not_instructions"]:
                refs.extend(json.loads(event["observation"])["source_ids"])
            action = {"kind": "finish", "evidence_ids": refs}
        return SimpleNamespace(
            model="fixture-model-not-a-provider",
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(action)))],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=10),
        )


class _ObservedCompletions:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.returned_model_ids: list[str | None] = []
        self.requested_temperatures: list[int | float] = []

    def create(self, **kwargs: Any) -> Any:
        self.requested_temperatures.append(kwargs["temperature"])
        response = self.delegate.create(**kwargs)
        model = getattr(response, "model", None)
        self.returned_model_ids.append(model if isinstance(model, str) and model.strip() else None)
        return response


def run_arm(
    arm: str,
    model_id: str,
    *,
    real_model: bool,
    max_provider_calls: int = _DEFAULT_ARM_CALLS,
    stop_after_reported_tokens: int = _DEFAULT_ARM_TOKENS,
) -> dict[str, Any]:
    """Run one frozen eight-case arm with a freshly seeded temporary SQLite DB."""
    if arm not in ARMS or not _MODEL_RE.fullmatch(model_id):
        raise ValueError("Unrecognized arm/model identity")
    if real_model:
        if not 1 <= max_provider_calls <= 64:
            raise ValueError("Context arm call cap must be between 1 and 64")
        from server.services.llm_gateway import governed_openai_client

        delegate = governed_openai_client(
            operation="planner",
            timeout=20.0,
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_API_BASE") or "",
        ).chat.completions
    else:
        delegate = _FakeCompletions()
    observed = _ObservedCompletions(delegate)
    client = SimpleNamespace(chat=SimpleNamespace(completions=observed))
    budget = (
        ProviderBudget(client, max_calls=max_provider_calls, stop_after_tokens=stop_after_reported_tokens)
        if real_model
        else None
    )
    if budget is not None:
        client = budget
    instances: list[ContextABPlanner] = []
    frozen_prompt = prompt_for_variant("baseline")

    def factory(profile: Any) -> ContextABPlanner:
        base = DecisionCardModelPlanner(client, model_id, profile, system_prompt=frozen_prompt)
        adapter = ContextABPlanner(base, arm)
        instances.append(adapter)
        return adapter

    cases = frozen_cases()
    with tempfile.TemporaryDirectory(prefix="gaobao-context-ab-") as directory:
        target = Path(directory) / "synthetic.db"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(target)
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=target, reset=True)
        business = run_cases(cases, planner_factory=factory, budget=budget)
        from db.database import engine

        engine.dispose()

    if budget is not None:
        receipt = budget.receipt()
        if (
            business["cases"] != _CASES
            or business["trace_validated_cases"] != _CASES
            or not receipt["usage_complete"]
            or receipt["stop_reason"] is not None
            or receipt["attempts"] != business["total_model_steps"]
            or receipt["known_prompt_tokens"] != business["provider_prompt_tokens"]
            or receipt["known_completion_tokens"] != business["provider_completion_tokens"]
        ):
            return {
                "contract": _CONTRACT,
                "arm": arm,
                "status": "INCOMPLETE_NOT_COMPARABLE",
                "controller": "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED",
                "model_id": model_id,
                "dataset_sha256": business["dataset_sha256"],
                "planned_cases": _CASES,
                "attempted_cases": business["cases"],
                "unattempted_case_ids": business["unattempted_case_ids"],
                "provider_budget": receipt,
                "production_release_authorized": False,
                "real_admissions_quality": "NOT_MEASURED",
            }
    if len(instances) != _CASES or business["cases"] != _CASES or business["holdout_cases"] != 4:
        raise ValueError("Incomplete frozen context evaluation suite")
    if business["trace_validated_cases"] != _CASES or any(value != 0 for value in observed.requested_temperatures):
        raise ValueError("Trace/temperature invariant failed")
    reported_models = sorted({model for model in observed.returned_model_ids if model is not None})
    origin_consistent = (
        len(reported_models) == 1
        and all(model is not None for model in observed.returned_model_ids)
        and len(observed.returned_model_ids) == sum(row["model_steps"] for row in business["results"])
    )
    rows = []
    for case, planner, row in zip(cases, instances, business["results"], strict=True):
        metrics = planner.public_context_stats()
        if metrics["decision_calls"] != row["model_steps"]:
            raise ValueError("Context decision accounting mismatches the execution trace")
        rows.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "passed": row["passed"],
                "safe_abstain": row["safe_abstain"],
                "failure_codes": row["failure_codes"],
                "execution_reason": row["execution_reason"],
                "elapsed_ms": row["elapsed_ms"],
                "tool_calls": row["tool_calls"],
                "model_steps": row["model_steps"],
                "trace_sha256": row["trace_receipt"]["trace_sha256"],
                "prompt_tokens": row["prompt_tokens"] if real_model else None,
                "completion_tokens": row["completion_tokens"] if real_model else None,
                **metrics,
            }
        )
    reported_prompt = business["provider_prompt_tokens"] if real_model else None
    reported_completion = business["provider_completion_tokens"] if real_model else None
    return {
        "contract": _CONTRACT,
        "arm": arm,
        "status": "COMPLETE",
        "provider_budget": budget.receipt() if budget is not None else None,
        "controller": "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED"
        if real_model
        else "SCRIPTED_COMPAT_STUB_NOT_LLM",
        "model_id": model_id if real_model else "SCRIPTED_NO_PROVIDER",
        "provider_reported_model": reported_models[0] if origin_consistent else None,
        "provider_model_identity_consistent": origin_consistent if real_model else False,
        "same_frozen_prompt_sha256": hashlib.sha256(frozen_prompt.encode()).hexdigest(),
        "tool_contract_sha256": _digest(
            {"tools": ["major_lookup", "admission_lookup", "plan_lookup"], "max_tool_calls": 5, "temperature": 0}
        ),
        "dataset_sha256": business["dataset_sha256"],
        "run_id": "synthetic_context_" + secrets.token_hex(12),
        "cases": _CASES,
        "holdout_cases": 4,
        "passed": business["passed"],
        "trace_validated_cases": business["trace_validated_cases"],
        "prompt_tokens_reported": reported_prompt,
        "completion_tokens_reported": reported_completion,
        "observation_chars_full": sum(row["history_full_chars"] for row in rows),
        "observation_chars_sent": sum(row["history_sent_chars"] for row in rows),
        "human_expert_ground_truth": False,
        "provider_billing_verified": False,
        "production_release_authorized": False,
        "real_admissions_quality": "NOT_MEASURED",
        "results": rows,
    }


def _index(report: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict) or report.get("contract") != _CONTRACT or report.get("arm") != arm:
        raise ValueError("Invalid or swapped context arm")
    if report.get("controller") not in {
        "SCRIPTED_COMPAT_STUB_NOT_LLM",
        "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED",
    }:
        raise ValueError("Unsupported execution provenance")
    if report.get("status") != "COMPLETE":
        raise ValueError("Incomplete context arm is not comparable")
    rows = report.get("results")
    if not isinstance(rows, list) or len(rows) != _CASES or report.get("cases") != _CASES:
        raise ValueError("Incomplete eight-case context suite")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError("Malformed result")
        if row["case_id"] in indexed or row.get("split") not in {"development", "holdout"}:
            raise ValueError("Duplicate case or missing holdout split")
        for field in (
            "history_full_chars",
            "history_sent_chars",
            "source_refs_preserved_across_steps",
            "compacted_observations_across_steps",
            "decision_calls",
            "tool_calls",
            "model_steps",
        ):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ValueError("Invalid context/step counter")
        if row["history_sent_chars"] > row["history_full_chars"] or row["decision_calls"] != row["model_steps"]:
            raise ValueError("Context size or step invariant violated")
        if type(row.get("passed")) is not bool or type(row.get("safe_abstain")) is not bool:
            raise ValueError("Missing task/abstention result")
        if not isinstance(row.get("trace_sha256"), str) or len(row["trace_sha256"]) != 64:
            raise ValueError("Missing trace digest")
        if not isinstance(row.get("failure_codes"), list) or not all(
            isinstance(code, str) for code in row["failure_codes"]
        ):
            raise ValueError("Malformed failure codes")
        if not isinstance(row.get("elapsed_ms"), (int, float)) or not math.isfinite(row["elapsed_ms"]):
            raise ValueError("Missing timing")
        indexed[row["case_id"]] = row
    if (
        sum(row["split"] == "holdout" for row in rows) != 4
        or sum(row["passed"] for row in rows) != report.get("passed")
        or report.get("trace_validated_cases") != _CASES
        or sum(row["history_full_chars"] for row in rows) != report.get("observation_chars_full")
        or sum(row["history_sent_chars"] for row in rows) != report.get("observation_chars_sent")
        or report.get("human_expert_ground_truth") is not False
        or report.get("production_release_authorized") is not False
    ):
        raise ValueError("Contradictory or incomplete context evaluation claims")
    real = report["controller"] == "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED"
    for field in ("prompt_tokens", "completion_tokens"):
        values = [row.get(field) for row in rows]
        if real:
            if any(type(value) is not int or value < 0 for value in values):
                raise ValueError("Provider usage missing; cannot compare real-model tokens")
            if sum(values) != report.get(f"{field}_reported"):
                raise ValueError("Provider usage total disagrees with case receipts")
        elif any(value is not None for value in values) or report.get(f"{field}_reported") is not None:
            raise ValueError("Scripted usage must not masquerade as provider tokens")
    receipt = report.get("provider_budget")
    if real:
        if (
            report.get("provider_model_identity_consistent") is not True
            or not isinstance(report.get("provider_reported_model"), str)
            or not report["provider_reported_model"].strip()
        ):
            raise ValueError("Provider-reported model identity missing or drifted")
        if not isinstance(receipt, dict) or (
            receipt.get("usage_complete") is not True
            or receipt.get("hard_token_cap") is not False
            or receipt.get("stop_reason") is not None
            or any(
                type(receipt.get(key)) is not int or receipt[key] < 0
                for key in (
                    "max_calls",
                    "stop_after_reported_tokens",
                    "attempts",
                    "known_usage_responses",
                    "known_prompt_tokens",
                    "known_completion_tokens",
                )
            )
            or not 1 <= receipt["max_calls"] <= 64
            or not 1 <= receipt["stop_after_reported_tokens"] <= 500_000
            or not 0 < receipt["attempts"] < receipt["max_calls"]
            or receipt["attempts"] != receipt["known_usage_responses"]
            or receipt["attempts"] != sum(row["model_steps"] for row in rows)
            or receipt["known_prompt_tokens"] != report["prompt_tokens_reported"]
            or receipt["known_completion_tokens"] != report["completion_tokens_reported"]
            or receipt["known_prompt_tokens"] + receipt["known_completion_tokens"]
            >= receipt["stop_after_reported_tokens"]
        ):
            raise ValueError("Missing or inconsistent provider budget receipt")
    elif receipt is not None:
        raise ValueError("Scripted arm cannot carry provider budget evidence")
    return indexed


def compare_pair(full: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any]:
    left = _index(full, ARMS[0])
    right = _index(compact, ARMS[1])
    if (
        full["run_id"] == compact["run_id"]
        or full["controller"] != compact["controller"]
        or any(
            full.get(field) != compact.get(field)
            for field in ("model_id", "dataset_sha256", "same_frozen_prompt_sha256", "tool_contract_sha256")
        )
        or set(left) != set(right)
        or any(left[key]["split"] != right[key]["split"] for key in left)
    ):
        raise ValueError("Unpaired model/dataset/prompt/tool/case identity")
    real = full["controller"] == "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED"
    if real and any(
        full["provider_budget"][key] != compact["provider_budget"][key]
        for key in ("max_calls", "stop_after_reported_tokens")
    ):
        raise ValueError("Provider budget differs between arms")
    reported_version_equal = (
        real
        and full.get("provider_model_identity_consistent") is True
        and compact.get("provider_model_identity_consistent") is True
        and isinstance(full.get("provider_reported_model"), str)
        and bool(full["provider_reported_model"].strip())
        and full["provider_reported_model"] == compact.get("provider_reported_model")
    )
    if real and not reported_version_equal:
        raise ValueError("Provider-reported model identity missing or drifted")
    groups: dict[str, dict[str, Any]] = {}
    for split in ("development", "holdout"):
        case_ids = sorted(key for key in left if left[key]["split"] == split)
        regressions = [
            key
            for key in case_ids
            if (left[key]["passed"] and not right[key]["passed"])
            or (left[key]["safe_abstain"] and not right[key]["safe_abstain"])
        ]
        base_tokens = (
            sum(left[key]["prompt_tokens"] + left[key]["completion_tokens"] for key in case_ids) if real else None
        )
        candidate_tokens = (
            sum(right[key]["prompt_tokens"] + right[key]["completion_tokens"] for key in case_ids) if real else None
        )
        groups[split] = {
            "cases": len(case_ids),
            "full_passed": sum(left[key]["passed"] for key in case_ids),
            "compact_passed": sum(right[key]["passed"] for key in case_ids),
            "full_safe_abstentions": sum(left[key]["safe_abstain"] for key in case_ids),
            "compact_safe_abstentions": sum(right[key]["safe_abstain"] for key in case_ids),
            "full_provider_tokens": base_tokens,
            "compact_provider_tokens": candidate_tokens,
            "provider_token_delta": candidate_tokens - base_tokens if real else None,
            "full_observation_chars": sum(left[key]["history_sent_chars"] for key in case_ids),
            "compact_observation_chars": sum(right[key]["history_sent_chars"] for key in case_ids),
            "full_total_elapsed_ms": round(sum(left[key]["elapsed_ms"] for key in case_ids), 2),
            "compact_total_elapsed_ms": round(sum(right[key]["elapsed_ms"] for key in case_ids), 2),
            "full_tool_calls": sum(left[key]["tool_calls"] for key in case_ids),
            "compact_tool_calls": sum(right[key]["tool_calls"] for key in case_ids),
            "regressed_case_ids": regressions,
        }
    holdout = groups["holdout"]
    return {
        "contract": "SYNTHETIC_CONTEXT_PAIRED_COMPARISON_V1",
        "controller": full["controller"],
        "model_id": full["model_id"],
        "full_run_id": full["run_id"],
        "compact_run_id": compact["run_id"],
        "provider_reported_model_equal": reported_version_equal if real else "NOT_MEASURED",
        "provider_reported_model": full["provider_reported_model"] if real else None,
        "groups": groups,
        "holdout_no_regression": not holdout["regressed_case_ids"],
        "holdout_provider_token_reduction": holdout["provider_token_delta"] < 0 if real else "NOT_MEASURED",
        "model_effectiveness_established": False,
        "real_admissions_accuracy": "NOT_MEASURED",
        "provider_billing_verified": False,
        "production_release_authorized": False,
    }


def compare_trials(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 5:
        raise ValueError("Expected 1..5 complete paired trials")
    results = []
    runs: set[str] = set()
    frozen_identity = None
    for index, pair in enumerate(pairs, 1):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("Malformed paired trial")
        full, compact = pair
        result = compare_pair(full, compact)
        identity = (
            result["controller"],
            result["model_id"],
            full["dataset_sha256"],
            full["same_frozen_prompt_sha256"],
            full["tool_contract_sha256"],
            result["provider_reported_model"],
            tuple(full["provider_budget"][key] for key in ("max_calls", "stop_after_reported_tokens"))
            if full["provider_budget"] is not None
            else None,
        )
        if frozen_identity is None:
            frozen_identity = identity
        if identity != frozen_identity or full["run_id"] in runs or compact["run_id"] in runs:
            raise ValueError("Trials reused runs or changed frozen evaluation contract")
        runs.update((full["run_id"], compact["run_id"]))
        results.append({"trial": index, **result})
    real = frozen_identity[0] == "EXTERNAL_MODEL_CALLS_NOT_INDEPENDENTLY_ATTESTED"
    no_regression = all(r["holdout_no_regression"] for r in results)
    repeatable_token_reduction = (
        real and len(results) >= 2 and all(r["holdout_provider_token_reduction"] is True for r in results)
    )
    token_deltas = [
        r["groups"]["holdout"]["provider_token_delta"]
        for r in results
        if isinstance(r["groups"]["holdout"]["provider_token_delta"], int)
    ]
    return {
        "contract": "SYNTHETIC_CONTEXT_REPEATED_COMPARISON_V1",
        "controller": frozen_identity[0],
        "model_id": frozen_identity[1],
        "dataset_sha256": frozen_identity[2],
        "provider_reported_model": frozen_identity[5],
        "trial_count": len(results),
        "holdout_observations_per_arm": len(results) * 4,
        "holdout_no_regression": no_regression,
        "every_trial_holdout_token_reduction": repeatable_token_reduction if real else "NOT_MEASURED",
        "median_holdout_token_delta": median(token_deltas) if token_deltas else None,
        "descriptive_candidate": "REVIEW_REQUIRED"
        if real and repeatable_token_reduction and no_regression
        else "NOT_ELIGIBLE_SCRIPTED"
        if not real
        else "REJECT_CANDIDATE",
        "paired_results": results,
        "real_user_quality": "NOT_MEASURED",
        "independent_expert_ground_truth": False,
        "provider_origin_independently_attested": False,
        "production_release_authorized": False,
        "statistical_significance": "NOT_ESTABLISHED",
    }


def _run_child(
    arm: str,
    mode: str,
    model: str,
    path: Path,
    allow_charges: bool,
    max_provider_calls: int,
    stop_after_reported_tokens: int,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "benchmarks.context_ab_eval",
        "--internal-arm",
        arm,
        "--model",
        model,
        "--out",
        str(path),
    ]
    if mode == "model":
        cmd.extend(
            (
                "--execute",
                "--permit-external-model",
                "--confirm-provider-charges",
                "--max-provider-calls-per-arm",
                str(max_provider_calls),
                "--stop-after-reported-tokens-per-arm",
                str(stop_after_reported_tokens),
            )
        )
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Context A/B arm exceeded the execution timeout") from exc
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError("Context arm failed; no complete, eligible receipt produced")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "COMPLETE":
        raise RuntimeError("Context arm stopped; inspect retained private receipt before any further calls")
    _index(report, arm)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Explicit non-floating model/version identifier")
    parser.add_argument("--trials", type=int, default=2, help="2..5 paired trials for real-model mode")
    parser.add_argument("--max-provider-calls-per-arm", type=int, default=_DEFAULT_ARM_CALLS)
    parser.add_argument("--stop-after-reported-tokens-per-arm", type=int, default=_DEFAULT_ARM_TOKENS)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--out", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--internal-arm", choices=ARMS, help=argparse.SUPPRESS)
    parser.add_argument("--scripted-smoke", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--permit-external-model", action="store_true")
    parser.add_argument("--confirm-provider-charges", action="store_true")
    args = parser.parse_args(argv)

    if not 1 <= args.max_provider_calls_per_arm <= 64 or not 1 <= args.stop_after_reported_tokens_per_arm <= 500_000:
        parser.error("Invalid provider budget")
    if not 2 <= args.trials <= 5:
        parser.error("--trials must be between 2 and 5")
    if args.execute:
        if not (
            args.permit_external_model
            and args.confirm_provider_charges
            and isinstance(args.model, str)
            and _MODEL_RE.fullmatch(args.model)
            and os.environ.get("OPENAI_API_KEY")
            and os.environ.get("APP_ENV", "development").lower() != "production"
        ):
            parser.error(
                "Real model requires --permit-external-model, --confirm-provider-charges, key, model and nonproduction"
            )
        if args.scripted_smoke:
            parser.error("Cannot mix scripted smoke with paid execution")
    elif args.permit_external_model or args.confirm_provider_charges:
        parser.error("Model permission and charge confirmation are invalid without --execute")
    if args.internal_arm is not None:
        if args.out is None or args.out_dir is not None or args.scripted_smoke:
            parser.error("Internal arm requires only a single explicit output file")
        if args.out.exists():
            parser.error("Refusing to overwrite an existing experiment report")
        if args.execute and not args.model:
            parser.error("External-model arm requires a model identifier")
        report = run_arm(
            args.internal_arm,
            args.model if args.execute else "fixture-stub",
            real_model=args.execute,
            max_provider_calls=args.max_provider_calls_per_arm,
            stop_after_reported_tokens=args.stop_after_reported_tokens_per_arm,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 0

    if not args.execute and not args.scripted_smoke:
        if args.out_dir is not None or args.out is not None:
            parser.error("Dry-run mode does not write artifacts")
        print(
            json.dumps(
                {
                    "mode": "DRY_RUN_NO_NETWORK",
                    "arms": ARMS,
                    "model_id": args.model,
                    "cases_per_arm": _CASES,
                    "planned_trials": args.trials,
                    "max_decisions_per_case": _STEPS_UPPER_BOUND,
                    "max_model_calls_exposure": len(ARMS) * args.trials * args.max_provider_calls_per_arm,
                    "max_provider_calls_per_arm": args.max_provider_calls_per_arm,
                    "stop_after_reported_tokens_per_arm": args.stop_after_reported_tokens_per_arm,
                    "hard_token_cap": False,
                    "provider_cost_usd": "UNKNOWN_NO_RATE_CARD",
                    "real_model_quality": "NOT_MEASURED",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.out_dir is None or args.out is not None:
        parser.error("A new --out-dir is required for executed experiments")
    if args.out_dir.exists() and (not args.out_dir.is_dir() or any(args.out_dir.iterdir())):
        parser.error("Output directory must not contain previous run artifacts")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    real_model = args.execute
    runs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    total_trials = args.trials if real_model else 1
    for trial in range(1, total_trials + 1):
        order = ARMS if trial % 2 else tuple(reversed(ARMS))
        reports: dict[str, dict[str, Any]] = {}
        for arm in order:
            path = args.out_dir / f"trial-{trial}-{arm}.json"
            reports[arm] = _run_child(
                arm,
                "model" if real_model else "stub",
                args.model or "fixture-stub",
                path,
                real_model,
                args.max_provider_calls_per_arm,
                args.stop_after_reported_tokens_per_arm,
            )
        runs.append((reports[ARMS[0]], reports[ARMS[1]]))
    summary = compare_trials(runs)
    target = args.out_dir / "context-ab-summary.json"
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Context A/B: {summary['descriptive_candidate']}; "
        f"trials={summary['trial_count']}; real_admissions_accuracy=NOT_MEASURED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
