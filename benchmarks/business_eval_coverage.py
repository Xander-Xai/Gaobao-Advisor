"""Coverage audit of frozen synthetic Agent business cases (no network, no DB).

Case-count/label balance is not real-model quality or real admissions coverage.
This audit prevents a small fixture from being presented as representative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.decision_card_business_eval import frozen_cases


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def evaluate_case_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(cases, list) or not cases:
        raise ValueError("Coverage needs a nonempty explicit case list")
    ids: set[str] = set()
    splits: dict[str, Counter[str]] = {}
    dimensions: dict[str, set[str]] = {
        "major": set(),
        "province": set(),
        "subject_type": set(),
        "year": set(),
        "school": set(),
    }
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("profile"), dict):
            raise ValueError("Malformed synthetic case")
        case_id, split, label = case.get("case_id"), case.get("split"), case.get("expected_status")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError("Missing or duplicate case_id")
        if split not in {"holdout", "development"} or label not in {
            "needs_review", "evidence_complete_historical_only"
        }:
            raise ValueError("Unknown split or expected business status")
        if case.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Only explicitly synthetic cases are supported")
        ids.add(case_id)
        splits.setdefault(split, Counter())[label] += 1
        profile = case["profile"]
        for field in ("major", "province", "subject_type", "year"):
            if field not in profile or profile[field] is None:
                raise ValueError(f"Missing {field} dimension")
            dimensions[field].add(str(profile[field]))
        for field in ("school_a", "school_b"):
            if not isinstance(profile.get(field), str) or not profile[field]:
                raise ValueError("Missing school identity")
            dimensions["school"].add(profile[field])
    missing_splits = sorted({"holdout", "development"}.difference(splits))
    gaps = [f"missing_{split}_split" for split in missing_splits]
    for name in ("major", "subject_type", "year"):
        if len(dimensions[name]) <= 1:
            gaps.append(f"single_{name}_coverage")
    if len(cases) < 30:
        gaps.append("small_synthetic_sample_count")
    gaps.append("no_real_admissions_ground_truth")
    return {
        "kind": "SYNTHETIC_EVAL_CASE_COVERAGE_AUDIT",
        "dataset_profile": "SYNTHETIC_DEMO",
        "dataset_sha256": _digest(cases),
        "sample_count": len(cases),
        "split_status_counts": {
            split: dict(sorted(counter.items())) for split, counter in sorted(splits.items())
        },
        "distinct_dimension_counts": {
            field: len(values) for field, values in sorted(dimensions.items())
        },
        "coverage_gaps": sorted(gaps),
        "independent_human_review": False,
        "real_admissions_accuracy": "NOT_MEASURED",
        "model_task_success": "NOT_MEASURED",
        "production_deployment_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate_case_coverage(frozen_cases())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"audited {report['sample_count']} synthetic cases: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
