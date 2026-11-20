"""Synthetic candidate generation is not automatic human ground truth approval."""

import copy
import json
import subprocess
import sys

import pytest

from benchmarks.groundtruth_pipeline import generate_candidates, validate_reviewed_export, verify_reviewed_candidates


def approvals_for(draft, reviewer="fixture-reviewer"):
    return [
        {
            "case_id": item["id"],
            "case_sha256": item["case_sha256"],
            "reviewed_by": reviewer,
            "reviewed_at": "2026-11-20T10:00:00+08:00",
        }
        for item in draft["cases"]
    ]


def test_candidates_are_deterministic_unapproved_and_split_by_entity():
    a = generate_candidates()
    b = generate_candidates()
    assert a == b
    assert a["dataset_kind"] == "synthetic_draft_not_ground_truth"
    assert len(a["cases"]) == 20
    assert sum(x["split"] == "holdout" for x in a["cases"]) == 5
    assert all(x["review_state"] == "PENDING_HUMAN_REVIEW" for x in a["cases"])


def test_review_gate_checks_every_case_and_retains_identity_caveat():
    draft = generate_candidates()
    verified = verify_reviewed_candidates(draft, approvals_for(draft))
    assert verified["case_count"] == 20
    assert verified["holdout_count"] == 5
    assert verified["development_count"] == 15
    assert verified["review_identity_independently_verified"] is False


def test_unreviewed_drafts_cannot_be_promoted():
    draft = generate_candidates()
    with pytest.raises(ValueError, match="Missing reviewed"):
        verify_reviewed_candidates(draft, approvals_for(draft)[:-1])


def test_forged_model_self_approval_is_rejected():
    draft = generate_candidates()
    with pytest.raises(ValueError, match="human review"):
        verify_reviewed_candidates(draft, approvals_for(draft, reviewer="LLM"))


def test_modified_label_with_stale_digest_is_rejected():
    draft = generate_candidates()
    copied = copy.deepcopy(draft)
    copied["cases"][0]["expected_facts"][0]["province"] = "示例省·丁"
    with pytest.raises(ValueError, match="manifest changed"):
        verify_reviewed_candidates(copied, approvals_for(copied))


def test_reviewed_export_rejects_tampering_even_if_manifest_digest_is_preserved():
    draft = generate_candidates()
    reviewed = verify_reviewed_candidates(draft, approvals_for(draft))
    validate_reviewed_export(reviewed)
    tampered = copy.deepcopy(reviewed)
    tampered["cases"][0]["expected_facts"][0]["province"] = "示例省·丁"
    with pytest.raises(ValueError, match="modified"):
        validate_reviewed_export(tampered)


def test_cli_only_generates_pending_draft_by_default(tmp_path):
    output = tmp_path / "candidate.json"
    p = subprocess.run(
        [sys.executable, "-m", "benchmarks.groundtruth_pipeline", "--out", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert p.returncode == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["dataset_kind"] == "synthetic_draft_not_ground_truth"
    assert all(c["review_state"] == "PENDING_HUMAN_REVIEW" for c in record["cases"])
