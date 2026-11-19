"""Independently re-read fictional SQL before trusting an Agent TaskRun receipt.

The model's tool trace and a "completed" label are not proof of a business
result. This gate executes after any runner (including Deep Agents), outside
the model's tool context, and refuses to persist unverified decision cards.
The data remain SYNTHETIC_DEMO only; no real admissions advice is produced.
"""

from __future__ import annotations

from typing import Any

from server.agent.readonly_tools import _major, _require_demo_database
from server.agent.synthetic_decision_card import (
    DISCLAIMER,
    DecisionCardProfile,
    _fetch_row,
)


class UnverifiedTaskRunReceipt(ValueError):
    """The returned card is inconsistent with the current synthetic database."""


_CARD_KEYS = frozenset(
    {
        "status",
        "reason",
        "data_profile",
        "planner",
        "profile",
        "major_category",
        "schools",
        "missing_evidence",
        "observed_negative_evidence_count",
        "evidence_ids",
        "manual_checks",
        "execution",
        "disclaimer",
    }
)


def verify_taskrun_receipt(profile: DecisionCardProfile, card: dict[str, Any]) -> str:
    """Return a trusted terminal state, or raise without persisting the card.

    This is a second trusted SQL read, not an LLM-as-judge evaluation. It
    intentionally fails closed if the dataset changed during execution.
    """
    if not isinstance(card, dict) or set(card) != _CARD_KEYS:
        raise UnverifiedTaskRunReceipt("Unexpected business receipt schema")
    if card["data_profile"] != "SYNTHETIC_DEMO" or card["disclaimer"] != DISCLAIMER:
        raise UnverifiedTaskRunReceipt("Missing synthetic data boundary")
    expected_profile = {
        "province": profile.province,
        "score": profile.score,
        "subject_type": profile.subject_type,
        "major": profile.major,
        "year": profile.year,
    }
    if card["profile"] != expected_profile or not isinstance(card["execution"], dict):
        raise UnverifiedTaskRunReceipt("Receipt is not bound to the approved profile")
    if not isinstance(card["manual_checks"], list) or not card["manual_checks"]:
        raise UnverifiedTaskRunReceipt("Missing manual decision safeguards")
    _require_demo_database()
    major = _major({"major_name": profile.major})
    if major.get("data_profile") != "SYNTHETIC_DEMO":
        raise UnverifiedTaskRunReceipt("Major evidence has the wrong data profile")

    expected_schools: list[dict[str, Any]] = []
    required_ids: list[str] = list(major.get("source_ids", []))
    supported = major.get("record_count") == 1 and bool(major.get("category"))
    supported = supported and len(required_ids) == 1
    for school in (profile.school_a, profile.school_b):
        args = {
            "school_name": school,
            "major_name": profile.major,
            "province": profile.province,
            "year": profile.year,
        }
        admission = _fetch_row("admission", args)
        plan = _fetch_row("plan", args)
        if admission.get("data_profile") != "SYNTHETIC_DEMO" or plan.get("data_profile") != "SYNTHETIC_DEMO":
            raise UnverifiedTaskRunReceipt("Admissions evidence has the wrong data profile")
        refs = [*admission.get("source_ids", []), *plan.get("source_ids", [])]
        min_score = admission.get("min_score")
        min_rank = admission.get("min_rank")
        plan_count = plan.get("plan_count")
        rule = plan.get("subject_requirement")
        constraint = "unknown"
        if rule == "不限":
            constraint = "compatible"
        elif rule == "物理必选":
            constraint = "compatible" if profile.subject_type == "物理类" else "incompatible"
        valid = (
            admission.get("record_count") == 1
            and plan.get("record_count") == 1
            and len(refs) == 2
            and type(min_score) is int
            and type(min_rank) is int
            and min_rank > 0
            and type(plan_count) is int
            and plan_count > 0
            and admission.get("subject_type") == profile.subject_type
            and constraint == "compatible"
        )
        supported = supported and valid
        required_ids.extend(refs)
        expected_schools.append(
            {
                "school_name": school,
                "historical_min_score": min_score,
                "historical_min_rank": min_rank,
                "historical_subject_type": admission.get("subject_type"),
                "historical_score_difference": (
                    profile.score - min_score if type(min_score) is int else None
                ),
                "plan_count": plan_count,
                "plan_subject_requirement": rule,
                "subject_constraint": constraint,
                "evidence_ids": refs,
            }
        )

    if card["status"] == "evidence_complete_historical_only":
        schools = card["schools"]
        refs = card["evidence_ids"]
        if not supported or len(required_ids) != 5 or len(set(required_ids)) != 5:
            raise UnverifiedTaskRunReceipt("Cannot complete without five independent source rows")
        if (
            schools != expected_schools
            or card["major_category"] != major["category"]
            or not isinstance(refs, list)
            or len(refs) != 5
            or set(refs) != set(required_ids)
            or card["missing_evidence"] != []
            or card["observed_negative_evidence_count"] != 0
        ):
            raise UnverifiedTaskRunReceipt("Business fields or source IDs do not match SQL")
        return "SUCCEEDED"

    if card["status"] == "needs_review":
        if (
            supported
            or card["schools"] != []
            or card["evidence_ids"] != []
            or not isinstance(card["missing_evidence"], list)
            or not card["missing_evidence"]
            or card["reason"] != "explicit_abstention"
            or type(card["observed_negative_evidence_count"]) is not int
            or card["observed_negative_evidence_count"] < 1
        ):
            raise UnverifiedTaskRunReceipt("Refusal lacks a verified negative SQL condition")
        return "NEEDS_REVIEW"

    raise UnverifiedTaskRunReceipt("Unsupported terminal business state")
