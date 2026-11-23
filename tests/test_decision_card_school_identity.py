"""Negative controls for independent SQL business-card grading.

These synthetic cards intentionally carry complete top-level evidence IDs while
repeating or substituting a school row. The oracle must not mistake those cards
for a valid two-school comparison. No model, network or real applicant data.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from benchmarks.decision_card_business_eval import frozen_cases, grade_business_case


def _fixture():
    case = next(c for c in frozen_cases() if c["expected_status"] == "evidence_complete_historical_only")
    a, b = case["profile"]["school_a"], case["profile"]["school_b"]
    assert a != b
    rows = {
        a: {"evidence_ids": ["synthetic:admission:aaaa", "synthetic:plan:aaaa"]},
        b: {"evidence_ids": ["synthetic:admission:bbbb", "synthetic:plan:bbbb"]},
    }
    refs = [
        "synthetic:major:" + hashlib.sha256(case["profile"]["major"].encode()).hexdigest()[:12],
        *rows[a]["evidence_ids"],
        *rows[b]["evidence_ids"],
    ]
    return case, rows, refs, a, b


def _card(names, rows, refs):
    return {
        "status": "evidence_complete_historical_only",
        "data_profile": "SYNTHETIC_DEMO",
        "schools": [
            {"school_name": name, **rows[name]}
            if name in rows else {"school_name": name, "evidence_ids": []}
            for name in names
        ],
        "evidence_ids": refs,
    }


def test_distinct_requested_schools_with_complete_independent_sources_pass():
    case, rows, refs, a, b = _fixture()
    with patch("benchmarks.decision_card_business_eval.independent_sql_oracle", return_value=(
        "evidence_complete_historical_only", rows
    )):
        assessment = grade_business_case(case, _card([a, b], rows, refs))
    assert assessment == {"passed": True, "failure_codes": []}


def test_duplicate_school_does_not_pass_with_both_schools_top_level_sources():
    case, rows, refs, a, _ = _fixture()
    with patch("benchmarks.decision_card_business_eval.independent_sql_oracle", return_value=(
        "evidence_complete_historical_only", rows
    )):
        assessment = grade_business_case(case, _card([a, a], rows, refs))
    assert assessment["passed"] is False
    assert "school_identity_mismatch" in assessment["failure_codes"]


def test_substituted_school_is_not_accepted_as_requested_comparison():
    case, rows, refs, a, _ = _fixture()
    with patch("benchmarks.decision_card_business_eval.independent_sql_oracle", return_value=(
        "evidence_complete_historical_only", rows
    )):
        assessment = grade_business_case(case, _card([a, "unrequested-school"], rows, refs))
    assert assessment["passed"] is False
    assert "school_identity_mismatch" in assessment["failure_codes"]


def test_malformed_school_row_fails_closed_instead_of_crashing():
    case, rows, refs, a, _ = _fixture()
    card = _card([a, a], rows, refs)
    card["schools"][1] = {"school_name": [], "evidence_ids": []}
    with patch("benchmarks.decision_card_business_eval.independent_sql_oracle", return_value=(
        "evidence_complete_historical_only", rows
    )):
        assessment = grade_business_case(case, card)
    assert assessment["passed"] is False
    assert "school_identity_mismatch" in assessment["failure_codes"]
