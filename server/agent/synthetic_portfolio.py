"""Multi-candidate synthetic admissions evidence review, never an admission plan.

Offline deterministic SQL tool experiment. No real institutions, rank-based
chance estimates, subject-policy claims, writes, internet or cloud models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.readonly_tools import _major, _require_demo_database
from server.agent.synthetic_decision_card import DISCLAIMER, _fetch_row

_SCHOOLS = frozenset(row[0] for row in DEMO_SCHOOLS)
_MAJORS = frozenset(row[0] for row in DEMO_MAJORS)
_PROVINCES = frozenset({"示例省·甲", "示例省·乙", "示例省·丙", "示例省·丁"})


@dataclass(frozen=True)
class PortfolioRequest:
    province: str
    score: int
    subject_type: str
    year: int
    candidates: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (
            self.province not in _PROVINCES
            or type(self.score) is not int
            or not 100 <= self.score <= 750
            or self.subject_type not in {"物理类", "历史类"}
            or type(self.year) is not int
            or self.year != DEMO_YEAR
        ):
            raise ValueError("Only frozen fictional applicant fields are permitted")
        if not isinstance(self.candidates, tuple) or not 3 <= len(self.candidates) <= 8:
            raise ValueError("Between three and eight fictional options are required")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("Duplicate school/major candidate")
        if any(
            not isinstance(item, tuple) or len(item) != 2 or item[0] not in _SCHOOLS or item[1] not in _MAJORS
            for item in self.candidates
        ):
            raise ValueError("Portfolio only accepts the seeded fictional school/major catalog")


def _evaluate(request: PortfolioRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    _require_demo_database()
    majors: dict[str, dict[str, Any]] = {}
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    calls = 0
    for school, major in request.candidates:
        if major not in majors:
            majors[major] = _major({"major_name": major})
            calls += 1
        category = majors[major]
        args = {"school_name": school, "major_name": major, "province": request.province, "year": request.year}
        admission = _fetch_row("admission", args)
        plan = _fetch_row("plan", args)
        calls += 2
        reasons: list[str] = []
        if category.get("record_count") != 1 or not category.get("category") or len(category.get("source_ids", [])) != 1:
            reasons.append("major_evidence_missing")
        if admission.get("record_count") != 1 or len(admission.get("source_ids", [])) != 1:
            reasons.append("admission_evidence_missing_or_ambiguous")
        if plan.get("record_count") != 1 or len(plan.get("source_ids", [])) != 1:
            reasons.append("plan_evidence_missing_or_ambiguous")
        if admission.get("record_count") == 1:
            if admission.get("subject_type") != request.subject_type:
                reasons.append("historical_subject_mismatch")
            if type(admission.get("min_score")) is not int or type(admission.get("min_rank")) is not int:
                reasons.append("invalid_historical_score_or_rank")
            elif admission["min_rank"] <= 0:
                reasons.append("invalid_historical_rank")
        if plan.get("record_count") == 1:
            rule = plan.get("subject_requirement")
            if rule not in {"不限", "物理必选"}:
                reasons.append("unknown_subject_rule")
            elif rule == "物理必选" and request.subject_type != "物理类":
                reasons.append("subject_requirement_mismatch")
            if type(plan.get("plan_count")) is not int or plan["plan_count"] <= 0:
                reasons.append("invalid_plan_count")
        if reasons:
            excluded.append({"school_name": school, "major_name": major, "reason_codes": sorted(set(reasons))})
            continue
        sources = [*category["source_ids"], *admission["source_ids"], *plan["source_ids"]]
        if len(sources) != 3 or len(set(sources)) != 3:
            excluded.append({"school_name": school, "major_name": major, "reason_codes": ["duplicate_source"]})
            continue
        accepted.append(
            {
                "school_name": school,
                "major_name": major,
                "major_category": category["category"],
                "historical_min_score": admission["min_score"],
                "historical_min_rank": admission["min_rank"],
                "historical_score_difference": request.score - admission["min_score"],
                "plan_count": plan["plan_count"],
                "subject_requirement": plan["subject_requirement"],
                "source_ids": sources,
            }
        )
    return accepted, excluded, calls


def build_synthetic_portfolio(request: PortfolioRequest) -> dict[str, Any]:
    """Classify every supplied choice; never fabricate a complete admissions plan."""
    accepted, excluded, calls = _evaluate(request)
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "status": "historical_candidates_reviewed" if accepted else "needs_review",
        "request": {
            "province": request.province,
            "score": request.score,
            "subject_type": request.subject_type,
            "year": request.year,
            "candidates": [list(item) for item in request.candidates],
        },
        "evaluated_count": len(request.candidates),
        "eligible_history_count": len(accepted),
        "excluded_count": len(excluded),
        "accepted_historical_comparisons": accepted,
        "excluded_candidates": excluded,
        "sql_tool_calls": calls,
        "admission_probability": None,
        "application_ranking": None,
        "applicant_rank": None,
        "human_review_complete": False,
        "disclaimer": DISCLAIMER,
        "warning": "Historical SQL evidence is not eligibility verification for current admissions or a submitted plan.",
    }


def verify_synthetic_portfolio(request: PortfolioRequest, receipt: dict[str, Any]) -> bool:
    """Re-read the isolated synthetic database and reject altered SQL receipts.

    A second SQL read is not an independently reviewed human ground truth.
    """
    if not isinstance(receipt, dict) or set(receipt) != set(build_synthetic_portfolio(request)):
        return False
    return receipt == build_synthetic_portfolio(request)
