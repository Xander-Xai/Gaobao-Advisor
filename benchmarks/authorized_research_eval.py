"""Evaluate authorized admissions research against independently reviewed gold labels.

Offline only: reads private real-data receipts from the existing business entry
point, never calls a provider, starts Docker or exports raw database rows.
A structural pass is not an attestation of data licensing or prediction quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from server.agent.admissions_deepagents import ResearchScope

SCHEMA = "authorized-admissions-research-gold/v1"
SHA = re.compile(r"[0-9a-f]{64}")
CASE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
OUTCOMES = {"VERIFIED_RESEARCH", "VERIFIED_PARTIAL_RESEARCH", "BLOCKED"}
DIMS = {"admission-history", "enrollment-plans"}
MAX_BYTES = 4 * 1024 * 1024


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def read_private_json(path: Path) -> Any:
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Input exceeds file budget")
    return json.loads(data, object_pairs_hook=_unique)


def _hash(value: Any) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _scope(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "school_ids",
        "major_id",
        "province",
        "subject_type",
        "batch",
        "year_from",
        "year_to",
    }:
        raise ValueError("Invalid scope fields")
    if not isinstance(value["school_ids"], (tuple, list)):
        raise ValueError("Invalid school IDs")
    ResearchScope(**value)


def validate_manifest(manifest: Any) -> list[dict]:
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "dataset_ref", "review_ref", "cases"}
        or manifest["schema"] != SCHEMA
    ):
        raise ValueError("Invalid gold manifest")
    for field in ("dataset_ref", "review_ref"):
        if not isinstance(manifest[field], str) or not 1 <= len(manifest[field].strip()) <= 200:
            raise ValueError("Dataset and independent review references required")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not 4 <= len(cases) <= 50:
        raise ValueError("Provide 4..50 independently reviewed cases")
    ids = set()
    scopes = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "split",
            "scope",
            "expected_status",
            "expected_coverage",
            "expected_analysis_sha256",
            "expected_evidence_sha256",
            "source_ref",
        }:
            raise ValueError("Invalid gold case")
        key = case["case_id"]
        if not isinstance(key, str) or not CASE.fullmatch(key) or key in ids:
            raise ValueError("Invalid or repeated case ID")
        ids.add(key)
        _scope(case["scope"])
        # Reversed comparison school order is the same cohort; never split it
        # between development and holdout.
        scope_key = digest({**case["scope"], "school_ids": sorted(case["scope"]["school_ids"])})
        if scope_key in scopes:
            raise ValueError("Repeated cohort leaks between cases")
        scopes.add(scope_key)
        if case["split"] not in {"development", "holdout"} or case["expected_status"] not in OUTCOMES:
            raise ValueError("Invalid split or expected status")
        if not isinstance(case["source_ref"], str) or not 1 <= len(case["source_ref"].strip()) <= 200:
            raise ValueError("Independent source reference required")
        if case["expected_status"] == "BLOCKED":
            if any(
                case[field] is not None
                for field in ("expected_coverage", "expected_analysis_sha256", "expected_evidence_sha256")
            ):
                raise ValueError("Blocked case cannot carry a verified gold result")
        else:
            coverage = case["expected_coverage"]
            if not isinstance(coverage, dict) or set(coverage) != DIMS:
                raise ValueError("Independent coverage labels required")
            for dim in coverage.values():
                if not isinstance(dim, dict) or set(dim) != {"missing", "ambiguous", "incomplete"}:
                    raise ValueError("Invalid coverage labels")
                for values in dim.values():
                    if not isinstance(values, list) or any(
                        not isinstance(pair, list) or len(pair) != 2 or any(type(x) is not int for x in pair)
                        for pair in values
                    ):
                        raise ValueError("Invalid coverage coordinates")
            partial = any(values for dim in coverage.values() for values in dim.values())
            if partial != (case["expected_status"] == "VERIFIED_PARTIAL_RESEARCH"):
                raise ValueError("Gold status and coverage disagree")
            if not _hash(case["expected_analysis_sha256"]) or not _hash(case["expected_evidence_sha256"]):
                raise ValueError("Independent gold evidence and analysis digests required")
    for split in ("development", "holdout"):
        group = [case for case in cases if case["split"] == split]
        if not any(case["expected_status"] == "VERIFIED_RESEARCH" for case in group) or not any(
            case["expected_status"] != "VERIFIED_RESEARCH" for case in group
        ):
            raise ValueError("Each split must include positive and incomplete/negative cases")
    return cases


def score_case(case: dict, receipt: Any) -> dict:
    reasons = []
    if not isinstance(receipt, dict) or receipt.get("schema") != "admissions-research/v1":
        return {"passed": False, "reasons": ["invalid_receipt"], "usage": None}
    if canonical(receipt.get("scope")) != canonical(case["scope"]):
        reasons.append("scope_mismatch")
    if receipt.get("engine") != "deepagents":
        reasons.append("not_sdk_business_path")
    if receipt.get("admission_recommendation_verified") is not False:
        reasons.append("unsupported_prediction_claim")
    status = receipt.get("status")
    if status != case["expected_status"]:
        reasons.append("unexpected_status")
    if case["expected_status"] == "BLOCKED":
        if any(receipt.get(key) is not None for key in ("evidence", "sandbox_analysis", "receipt_ids")):
            reasons.append("blocked_result_leaked")
    else:
        evidence = receipt.get("evidence")
        if (
            not isinstance(evidence, dict)
            or set(evidence) != DIMS
            or receipt.get("evidence_sha256") != digest(evidence)
            or receipt.get("evidence_sha256") != case["expected_evidence_sha256"]
        ):
            reasons.append("gold_evidence_mismatch")
        if canonical(receipt.get("coverage")) != canonical(case["expected_coverage"]):
            reasons.append("gold_coverage_mismatch")
        analysis = receipt.get("sandbox_analysis")
        if (
            not isinstance(analysis, dict)
            or analysis.get("status") != "VERIFIED_DESCRIPTIVE_ANALYSIS"
            or analysis.get("cleanup_verified") is not True
            or analysis.get("result_sha256") != digest(analysis.get("result"))
            or analysis.get("result_sha256") != case["expected_analysis_sha256"]
        ):
            reasons.append("gold_sandbox_mismatch")
    return {"passed": not reasons, "reasons": reasons, "usage": _declared_usage(receipt.get("provider_usage"))}


def _declared_usage(usage):
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0 for key in ("input_tokens", "output_tokens")
    ):
        return None
    return {key: usage[key] for key in ("input_tokens", "output_tokens")}


def evaluate(manifest: Any, receipts: Any) -> dict:
    cases = validate_manifest(manifest)
    if not isinstance(receipts, dict) or set(receipts) != {case["case_id"] for case in cases}:
        raise ValueError("Exactly one receipt per gold case required; no case cherry-picking")
    task_ids = [receipt.get("task_id") for receipt in receipts.values() if isinstance(receipt, dict)]
    if (
        len(task_ids) != len(cases)
        or any(not isinstance(key, str) or not re.fullmatch(r"research_[0-9a-f]{32}", key) for key in task_ids)
        or len(set(task_ids)) != len(task_ids)
    ):
        raise ValueError("Each case needs a distinct research run")
    results = [
        {"case_id": case["case_id"], "split": case["split"], **score_case(case, receipts[case["case_id"]])}
        for case in cases
    ]
    return _summarize(manifest, results)


def _summarize(manifest, results):
    splits = {}
    for split in ("development", "holdout"):
        items = [item for item in results if item["split"] == split]
        usage_complete = all(item["usage"] is not None for item in items)
        splits[split] = {
            "cases": len(items),
            "passed": sum(item["passed"] for item in items),
            "provider_usage_declared": (
                {key: sum(item["usage"][key] for item in items) for key in ("input_tokens", "output_tokens")}
                if usage_complete
                else None
            ),
        }
    return {
        "schema": "authorized-admissions-research-eval/v1",
        "manifest_sha256": digest(manifest),
        "dataset_ref_sha256": digest(manifest["dataset_ref"]),
        "review_ref_sha256": digest(manifest["review_ref"]),
        "splits": splits,
        "cases": results,
        "status": "REVIEW_REQUIRED" if all(item["passed"] for item in results) else "FAILED",
        "source_provenance_independently_verified": False,
        "provider_billing_verified": False,
        "admission_prediction_accuracy": "NOT_MEASURED",
        "production_release_authorized": False,
    }


def evaluate_managed(manifest, run_map, journal_path, *, model_id, image_id):
    """Grade the managed lifecycle's verified aggregate projection without raw SQL export.

    An unclassified failed run is never credit for an expected safe rejection.
    This lane cannot independently recompute raw evidence or attest provider transport.
    """
    from server.agent.admissions_research_taskrun import ID, IMAGE, MODEL_RE, read_managed_eval_views

    cases = validate_manifest(manifest)
    if (
        not isinstance(run_map, dict)
        or set(run_map) != {case["case_id"] for case in cases}
        or any(not isinstance(value, str) or not ID.fullmatch(value) for value in run_map.values())
        or len(set(run_map.values())) != len(cases)
    ):
        raise ValueError("Exactly one distinct managed run per gold case required")
    if not isinstance(model_id, str) or not MODEL_RE.fullmatch(model_id):
        raise ValueError("Explicit evaluation model required")
    if not isinstance(image_id, str) or not IMAGE.fullmatch(image_id):
        raise ValueError("Explicit evaluation image required")
    views = read_managed_eval_views(journal_path, list(run_map.values()))
    results = []
    for case in cases:
        item = views[run_map[case["case_id"]]]
        spec, result = item["spec"], item["result"]
        reasons = []
        if canonical(spec["scope"]) != canonical(case["scope"]):
            reasons.append("scope_mismatch")
        if spec["dataset_ref"] != manifest["dataset_ref"] or spec["model_id"] != model_id:
            reasons.append("dataset_or_model_mismatch")
        if spec["image_id"] != image_id:
            reasons.append("image_mismatch")
        if not item["approved_by"] or item["approved_by"] == item["owner"]:
            reasons.append("independent_approval_missing")
        if case["expected_status"] == "BLOCKED":
            # The current managed journal intentionally collapses failure causes.
            # FAILED can be an outage, not proof of correct business rejection.
            reasons.append("managed_block_reason_not_independently_classified")
        else:
            expected_state = "SUCCEEDED" if case["expected_status"] == "VERIFIED_RESEARCH" else "NEEDS_REVIEW"
            if item["status"] != expected_state or not isinstance(result, dict):
                reasons.append("unexpected_managed_state")
            else:
                if result.get("status") != case["expected_status"] or result.get("spec_sha256") != item["spec_sha256"]:
                    reasons.append("result_binding_mismatch")
                if result.get("admission_recommendation_verified") is not False:
                    reasons.append("unsupported_prediction_claim")
                if canonical(result.get("coverage")) != canonical(case["expected_coverage"]):
                    reasons.append("gold_coverage_mismatch")
                if result.get("evidence_sha256") != case["expected_evidence_sha256"]:
                    reasons.append("gold_evidence_mismatch")
                if (
                    result.get("analysis_sha256") != digest(result.get("analysis"))
                    or result.get("analysis_sha256") != case["expected_analysis_sha256"]
                ):
                    reasons.append("gold_sandbox_mismatch")
                receipts = result.get("receipt_ids")
                if not isinstance(receipts, dict) or set(receipts) != DIMS or not all(
                    _hash(value) for value in receipts.values()
                ):
                    reasons.append("missing_verified_receipt_ids")
        results.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "passed": not reasons,
                "reasons": reasons,
                "usage": _declared_usage(result.get("provider_usage")) if isinstance(result, dict) else None,
            }
        )
    report = _summarize(manifest, results)
    report.update(
        {
            "evidence_lane": "MANAGED_JOURNAL_AGGREGATE",
            "run_map_sha256": digest(run_map),
            "approved_model_id": model_id,
            "approved_image_id": image_id,
            "raw_evidence_independently_recomputed": False,
            "provider_transport_verified": False,
        }
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--receipts", type=Path, help="Private full receipt JSON object keyed by case ID")
    inputs.add_argument("--managed-runs", type=Path, help="Private JSON object mapping case ID to managed run ID")
    parser.add_argument("--journal", type=Path, help="Existing private managed journal (read-only)")
    parser.add_argument("--expected-model")
    parser.add_argument("--expected-image")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--authorize-private-eval", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error("Output must be a new private evidence file")
    if args.managed_runs and not (args.journal and args.expected_model and args.expected_image):
        parser.error("Managed evaluation requires journal, expected model and expected image")
    if args.receipts and (args.journal or args.expected_model or args.expected_image):
        parser.error("Managed options require --managed-runs")
    try:
        manifest = read_private_json(args.gold)
        if args.managed_runs:
            result = evaluate_managed(
                manifest,
                read_private_json(args.managed_runs),
                args.journal,
                model_id=args.expected_model,
                image_id=args.expected_image,
            )
        else:
            result = evaluate(manifest, read_private_json(args.receipts))
    except (ValueError, TypeError, OSError, LookupError):
        parser.error("Invalid or unavailable approved evaluation input")
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(canonical(result) + "\n")
    return 0 if result["status"] == "REVIEW_REQUIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
