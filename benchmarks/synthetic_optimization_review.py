"""Development-split failure triage and measured-only optimization review proposal.

Inputs are *declared* external-model report files on fictional admissions SQL.
Full model execution traces are NOT available: the case reports only contain
structured outcome/usage metadata. No provider, billing, expert or origin
attestation happens here; no production deployment is ever authorized.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from benchmarks.decision_card_trace_eval import trace_failure_category
from benchmarks.harness_optimizer import propose_bounded_changes
from benchmarks.repeated_model_experiment import ARMS, compare_repeated_trials

_MAX_REPORT_BYTES = 160_000
_ALLOWED_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,59}$")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def build_optimization_review(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Recompute the holdout gate while deriving hypotheses ONLY from development.

    The exact case run metadata is hashed but not persisted in the returned
    review proposal; any future claim of an origin or external provider still
    requires independent evidence.
    """
    serialized = _canonical(pairs)
    if len(serialized.encode("utf-8")) > _MAX_REPORT_BYTES:
        raise ValueError("Optimization report bundle exceeds synthetic safety budget")
    summary = compare_repeated_trials(pairs)
    failures: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    trace_failures: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    trace_covered_cases = 0
    for pair in pairs:
        for arm, report in zip(ARMS, pair, strict=True):
            for case in report["results"]:
                if case["split"] != "development":
                    continue
                codes = case["failure_codes"]
                if len(codes) > 20 or not all(
                    isinstance(code, str) and _ALLOWED_FAILURE_CODE.fullmatch(code) for code in codes
                ):
                    raise ValueError("Invalid bounded failure categories in development cases")
                for code in set(codes):
                    failures[arm][code] += 1
                if report.get("trace_contract") is not None:
                    trace_covered_cases += 1
                    reason = trace_failure_category(case)
                    if reason is not None:
                        trace_failures[arm][reason] += 1
    baseline_codes = failures[ARMS[0]]
    hypotheses: list[str] = []
    for code in sorted(baseline_codes):
        if code in {"factual_mismatch", "source_contract_invalid", "incomplete_evidence", "outcome_mismatch"}:
            hypotheses.append("unverified_finish")
        elif code == "negative_evidence_not_observed":
            hypotheses.append("unverified_finish")
        elif code == "tool_repeat_loop":
            hypotheses.append("tool_repeat_loop")
        elif code == "reported_token_budget_exhausted":
            hypotheses.append("reported_token_budget_exhausted")
    proposals = propose_bounded_changes(hypotheses)
    # Configuration is an *untried suggestion*, not a free-form executable patch.
    # The only reviewable experiment arm is the existing frozen evidence_first.
    gate = summary["descriptive_candidate"]
    return {
        "kind": "SYNTHETIC_DECLARED_MODEL_OPTIMIZATION_REVIEW",
        "evidence_bundle_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "data_profile": "SYNTHETIC_DEMO",
        "model_id": summary["model_id"],
        "dataset_sha256": summary["dataset_sha256"],
        "tool_contract_sha256": summary["tool_contract_sha256"],
        "candidate_variant": "evidence_first",
        "candidate_prompt_sha256": summary["prompt_sha256_by_arm"]["evidence_first"],
        "trial_count": summary["trial_count"],
        "holdout_observations_per_arm": summary["holdout_observations_per_arm"],
        "holdout_quality_non_regression": summary["holdout_quality_non_regression"],
        "every_trial_token_reduction": summary["every_trial_token_reduction"],
        "baseline_holdout_verified_outcomes": summary["baseline_holdout_verified_outcomes"],
        "candidate_holdout_verified_outcomes": summary["candidate_holdout_verified_outcomes"],
        "holdout_provider_token_delta": summary["holdout_provider_token_delta"],
        "regressed_case_ids": summary["regressions"],
        "evaluation_gate": gate,
        "development_failure_clusters": {arm: dict(sorted(counts.items())) for arm, counts in failures.items()},
        "development_only_untested_hypotheses": proposals,
        "development_trace_covered_cases": trace_covered_cases,
        "development_trace_failures_by_arm": {
            arm: dict(sorted(counts.items())) for arm, counts in trace_failures.items()
        },
        "model_provenance_verified": False,
        "provider_billing_reconciled": False,
        "independent_human_ground_truth_verified": False,
        "holdout_statistical_significance": "NOT_ESTABLISHED",
        "real_admissions_accuracy": "NOT_MEASURED",
        "production_deployment_authorized": False,
        "scope_warning": (
            "Case-level summaries are not complete Agent traces; repeated small fictional "
            "holdouts are not independent cases or proof of improvement. "
            "Admin account attestation must not be described as expert GT."
        ),
    }
