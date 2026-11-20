"""Synthetic candidate ground truth with immutable case digests and review gate.

Generate from the seeded fictional source records, NOT from agent answers.
Candidates are never automatically marked "approved". Review metadata is a
declaration, not cryptographic proof of human identity; GitHub review is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def generate_candidates() -> dict[str, Any]:
    """Generate a reproducible draft from the synthetic seed, never from a model."""
    from scripts.seed_demo_data import DATASET_VERSION, build_payload

    payload = build_payload()
    if payload["metadata"]["marker"] != "SYNTHETIC_DEMO_DATA":
        raise ValueError("Refusing a non-synthetic source")
    rows: list[dict[str, Any]] = []
    for category in ("schools", "majors"):
        for idx, entry in enumerate(payload[category]):
            if category == "schools":
                name = entry["name"]
                field = {"tool": "school_lookup", "school_name": name, "province": entry["province"]}
                task = f"查询虚构院校{name}所在的演示省份"
                prefix = "school"
            else:
                name = entry["name"]
                field = {"tool": "major_lookup", "major_name": name, "category": entry["category"]}
                task = f"查询虚构专业{name}的演示学科门类"
                prefix = "major"
            if "（合成）" not in name:
                raise ValueError("Unexpected non-fictional entity name")
            base = {
                "id": f"{prefix}-{idx + 1:02d}",
                "task": task,
                "expected_facts": [field],
                "split": "holdout" if idx % 5 == 0 else "development",
                "origin": "SYNTHETIC_DEMO_SEED",
                "dataset_version": DATASET_VERSION,
            }
            rows.append({**base, "case_sha256": _digest(base), "review_state": "PENDING_HUMAN_REVIEW"})
    return {
        "schema_version": 1,
        "dataset_kind": "synthetic_draft_not_ground_truth",
        "source_generator": "scripts.seed_demo_data.build_payload",
        "cases": rows,
        "manifest_sha256": _digest(rows),
    }


def verify_reviewed_candidates(draft: dict[str, Any], approvals: list[dict[str, str]]) -> dict[str, Any]:
    """Validate every frozen case against explicit, nonempty review declarations.

    Callers MUST still confirm reviewers via code review: strings here cannot
    establish reviewer identity or the quality of their judgment.
    """
    if not isinstance(draft, dict) or draft.get("schema_version") != 1:
        raise ValueError("Unsupported draft")
    if draft.get("dataset_kind") != "synthetic_draft_not_ground_truth":
        raise ValueError("Draft must be generated and not already marked released")
    cases = draft.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 100:
        raise ValueError("Invalid case collection")
    if draft.get("manifest_sha256") != _digest(cases):
        raise ValueError("Draft manifest changed after generation")
    if not isinstance(approvals, list):
        raise ValueError("Approvals must be a list")
    approved = {}
    for item in approvals:
        if not isinstance(item, dict) or set(item) != {"case_id", "case_sha256", "reviewed_by", "reviewed_at"}:
            raise ValueError("Invalid review declaration")
        case_id = item["case_id"]
        reviewer = item["reviewed_by"]
        if (
            not isinstance(case_id, str)
            or not isinstance(reviewer, str)
            or len(reviewer.strip()) < 3
            or reviewer.lower() in {"ci", "generator", "automation", "model", "llm"}
            or case_id in approved
        ):
            raise ValueError("Missing or duplicate human review declaration")
        try:
            datetime.fromisoformat(item["reviewed_at"])
        except (ValueError, TypeError) as exc:
            raise ValueError("Review date must be ISO-8601") from exc
        approved[case_id] = item
    verified = []
    seen_ids: set[str] = set()
    splits: dict[str, set[str]] = {"development": set(), "holdout": set()}
    for item in cases:
        case_id = item.get("id")
        if not isinstance(case_id, str) or case_id in seen_ids:
            raise ValueError("Duplicate case in draft")
        seen_ids.add(case_id)
        fixed = {key: value for key, value in item.items() if key not in ("case_sha256", "review_state")}
        if item.get("review_state") != "PENDING_HUMAN_REVIEW" or item.get("case_sha256") != _digest(fixed):
            raise ValueError("Tampered case specification")
        split = item.get("split")
        if split not in splits:
            raise ValueError("Invalid data split")
        # Entity names are used as leakage keys; cases about the same entity
        # must not occur in both development and holdout partitions.
        facts = item.get("expected_facts")
        if not isinstance(facts, list) or len(facts) != 1:
            raise ValueError("Expected one synthetic fact per case")
        identity = facts[0].get("school_name", facts[0].get("major_name"))
        if not isinstance(identity, str):
            raise ValueError("Invalid synthetic identity")
        splits[split].add(identity)
        receipt = approved.get(case_id)
        if receipt is None or receipt["case_sha256"] != item["case_sha256"]:
            raise ValueError("Missing reviewed case approval or stale content hash")
        verified.append({**item, "review_state": "REVIEW_DECLARED", "reviewed_by": receipt["reviewed_by"]})
    if set(approved) != seen_ids:
        raise ValueError("Approvals refer to unknown cases")
    if not splits["development"] or not splits["holdout"] or splits["development"] & splits["holdout"]:
        raise ValueError("Missing or overlapping development/holdout entity sets")
    return {
        "dataset_kind": "synthetic_review_declared",
        "review_identity_independently_verified": False,
        "case_count": len(verified),
        "development_count": sum(x["split"] == "development" for x in verified),
        "holdout_count": sum(x["split"] == "holdout" for x in verified),
        "cases": verified,
        "source_sha256": draft["manifest_sha256"],
    }


def validate_reviewed_export(export: dict[str, Any]) -> None:
    """Reject re-labeled or tampered exported reviews before any model evaluation."""
    generated = generate_candidates()
    if (
        not isinstance(export, dict)
        or export.get("dataset_kind") != "synthetic_review_declared"
        or export.get("source_sha256") != generated["manifest_sha256"]
        or export.get("review_identity_independently_verified") is not False
    ):
        raise ValueError("Invalid or stale review declaration")
    expected = {row["id"]: row for row in generated["cases"]}
    cases = export.get("cases")
    if not isinstance(cases, list) or len(cases) != len(expected):
        raise ValueError("Review export does not contain the frozen case set")
    seen: set[str] = set()
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("Invalid reviewed case")
        identity = row.get("id")
        if identity not in expected or identity in seen:
            raise ValueError("Unknown or duplicated reviewed case ID")
        seen.add(identity)
        if row.get("review_state") != "REVIEW_DECLARED" or not isinstance(row.get("reviewed_by"), str):
            raise ValueError("Missing reviewer declaration")
        unchanged = {key: value for key, value in row.items() if key not in {"review_state", "reviewed_by"}}
        frozen = {key: value for key, value in expected[identity].items() if key != "review_state"}
        if unchanged != frozen:
            raise ValueError("Review export modified the frozen ground truth")
    if set(expected) != seen:
        raise ValueError("Review export has missing case IDs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--approvals", type=Path, help="Optional human review declarations file")
    args = parser.parse_args(argv)
    draft = generate_candidates()
    if args.approvals:
        reviews = json.loads(args.approvals.read_text(encoding="utf-8"))
        output = verify_reviewed_candidates(draft, reviews)
    else:
        output = draft
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output['dataset_kind']}: {len(draft['cases'])} fictional cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
