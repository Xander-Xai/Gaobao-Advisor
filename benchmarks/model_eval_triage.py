"""Offline, provenance-honest triage for the opt-in synthetic Agent model eval.

Consumes an existing 8-case model run. It never calls a provider, declares a
real-world admissions accuracy, or upgrades self-reported metadata to proof.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.decision_card_business_eval import _digest, frozen_cases
from benchmarks.model_run_comparison import _validate


def summarize_model_failures(report: dict[str, Any]) -> dict[str, Any]:
    """Explain which frozen synthetic tasks failed, without promoting the run."""
    _validate(report)
    # An eight-row run with four holdout rows is not necessarily our frozen
    # eight-task benchmark. Bind the declared run to the exact shipped suite,
    # without mistaking a matching self-reported digest for provider attestation.
    expected_cases = frozen_cases()
    if report["dataset_sha256"] != _digest(expected_cases):
        raise ValueError("Model report does not match the frozen synthetic suite digest")
    expected_identity = {case["case_id"]: case["split"] for case in expected_cases}
    actual_identity = {row["case_id"]: row["split"] for row in report["results"]}
    if actual_identity != expected_identity:
        raise ValueError("Model report case IDs or development/holdout splits changed")
    by_split: dict[str, dict[str, Any]] = {}
    failures = Counter()
    for split in ("development", "holdout"):
        rows = [r for r in report["results"] if r["split"] == split]
        failed = [r for r in rows if not r["passed"]]
        for row in failed:
            for code in set(row["failure_codes"]) or {"unspecified_failure"}:
                failures[code] += 1
        by_split[split] = {
            "cases": len(rows),
            "declared_oracle_passes": sum(r["passed"] for r in rows),
            "declared_safe_abstentions": sum(r["passed"] and r["safe_abstain"] for r in rows),
            "failed_case_ids": sorted(r["case_id"] for r in failed),
            "failed_cases": [
                {"case_id": r["case_id"], "failure_codes": sorted(set(r["failure_codes"]))}
                for r in sorted(failed, key=lambda row: row["case_id"])
            ],
            "provider_tokens_reported": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows),
        }
    return {
        "kind": "DECLARED_SYNTHETIC_MODEL_FAILURE_TRIAGE",
        "run_id": report["run_id"],
        "model_id": report["model_id"],
        "dataset_sha256": report["dataset_sha256"],
        "frozen_case_identity_checked": True,
        "frozen_suite_digest_checked": True,
        "by_split": by_split,
        "failure_code_counts": dict(sorted(failures.items())),
        "provenance_independently_verified": False,
        "human_review_complete": False,
        "real_admissions_accuracy": "NOT_MEASURED",
        "production_deployment_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        summary = summarize_model_failures(report)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(f"invalid model-run artifact: {type(exc).__name__}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"triage written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
