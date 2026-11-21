"""Independent SQL-grounded oracle for a *synthetic-only* portfolio receipt.

Uses sqlite3 read-only queries instead of the SQLAlchemy data tools, portfolio
classifier or second invocation of build_synthetic_portfolio. This is a
deterministic rule oracle for fictional rows, NOT human-reviewed ground truth
or a real admissions policy/eligibility determination.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from server.agent.synthetic_decision_card import DISCLAIMER
from server.agent.synthetic_portfolio import PortfolioRequest

_WARNING = "Historical SQL evidence is not eligibility verification for current admissions or a submitted plan."
_KEYS = frozenset(
    {
        "data_profile",
        "status",
        "request",
        "evaluated_count",
        "eligible_history_count",
        "excluded_count",
        "accepted_historical_comparisons",
        "excluded_candidates",
        "sql_tool_calls",
        "admission_probability",
        "application_ranking",
        "applicant_rank",
        "human_review_complete",
        "disclaimer",
        "warning",
    }
)


class UnverifiedPortfolioReceipt(ValueError):
    """Model- or tool-originated portfolio fields contradict direct SQL facts."""


def _row_source(kind: str, details: dict[str, Any], row_id: int) -> str:
    canonical = json.dumps(
        {"table": kind, "row_id": row_id, **details},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"synthetic:{kind}:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def _direct_sql(profile: PortfolioRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Query a read-only SQLite snapshot without using the Agent's data tools."""
    url = os.getenv("DATABASE_URL", "")
    if (
        not url.startswith("sqlite:///")
        or os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false"
        or os.getenv("APP_ENV", "development").lower() == "production"
    ):
        raise UnverifiedPortfolioReceipt("Independent rule oracle requires isolated fictional SQLite")
    path = Path(url.removeprefix("sqlite:///")).resolve()
    if not path.is_file():
        raise UnverifiedPortfolioReceipt("Fictional SQL dataset not found")
    db_uri = f"file:{quote(path.as_posix(), safe='/')}?mode=ro"
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    with sqlite3.connect(db_uri, uri=True, timeout=5.0) as db:
        db.row_factory = sqlite3.Row
        snapshot = db.execute("SELECT profile,is_synthetic FROM dataset_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if snapshot is None or snapshot["profile"] != "SYNTHETIC_DEMO" or snapshot["is_synthetic"] != 1:
            raise UnverifiedPortfolioReceipt("Untrusted dataset provenance")
        for school_name, major_name in profile.candidates:
            school = db.execute(
                "SELECT id,special_type,data_source_note FROM schools WHERE name=?",
                (school_name,),
            ).fetchone()
            major = db.execute(
                "SELECT id,category FROM majors WHERE name=?",
                (major_name,),
            ).fetchone()
            if (
                school is None
                or major is None
                or school["special_type"] != "SYNTHETIC_DEMO_DATA"
                or school["data_source_note"] != "合成演示数据，无真实来源"
            ):
                raise UnverifiedPortfolioReceipt("Fictional entity snapshot does not match approved catalog")
            args = (school["id"], major["id"], profile.province, profile.year)
            admissions = db.execute(
                "SELECT id,subject_type,min_score,min_rank,batch FROM admission_scores "
                "WHERE school_id=? AND major_id=? AND province=? AND year=? LIMIT 2",
                args,
            ).fetchall()
            plans = db.execute(
                "SELECT id,subject_requirement,plan_count,batch FROM enrollment_plans "
                "WHERE school_id=? AND major_id=? AND province=? AND year=? LIMIT 2",
                args,
            ).fetchall()
            reasons: list[str] = []
            if not isinstance(major["category"], str) or not major["category"]:
                reasons.append("major_evidence_missing")
            if len(admissions) != 1:
                reasons.append("admission_evidence_missing_or_ambiguous")
            if len(plans) != 1:
                reasons.append("plan_evidence_missing_or_ambiguous")
            if len(admissions) == 1:
                history = admissions[0]
                if history["subject_type"] != profile.subject_type:
                    reasons.append("historical_subject_mismatch")
                if type(history["min_score"]) is not int or type(history["min_rank"]) is not int:
                    reasons.append("invalid_historical_score_or_rank")
                elif history["min_rank"] <= 0:
                    reasons.append("invalid_historical_rank")
            if len(plans) == 1:
                plan = plans[0]
                if plan["subject_requirement"] not in {"不限", "物理必选"}:
                    reasons.append("unknown_subject_rule")
                elif plan["subject_requirement"] == "物理必选" and profile.subject_type != "物理类":
                    reasons.append("subject_requirement_mismatch")
                if type(plan["plan_count"]) is not int or plan["plan_count"] <= 0:
                    reasons.append("invalid_plan_count")
            if reasons:
                excluded.append(
                    {"school_name": school_name, "major_name": major_name, "reason_codes": sorted(set(reasons))}
                )
                continue
            details = {
                "school_name": school_name,
                "major_name": major_name,
                "province": profile.province,
                "year": profile.year,
            }
            sources = [
                "synthetic:major:" + hashlib.sha256(major_name.encode()).hexdigest()[:12],
                _row_source(
                    "admission",
                    {
                        **details,
                        "subject_type": history["subject_type"],
                        "min_score": history["min_score"],
                        "min_rank": history["min_rank"],
                        "batch": history["batch"],
                    },
                    history["id"],
                ),
                _row_source(
                    "plan",
                    {
                        **details,
                        "subject_requirement": plan["subject_requirement"],
                        "plan_count": plan["plan_count"],
                        "batch": plan["batch"],
                    },
                    plan["id"],
                ),
            ]
            if len(set(sources)) != 3:
                excluded.append(
                    {"school_name": school_name, "major_name": major_name, "reason_codes": ["duplicate_source"]}
                )
                continue
            accepted.append(
                {
                    "school_name": school_name,
                    "major_name": major_name,
                    "major_category": major["category"],
                    "historical_min_score": history["min_score"],
                    "historical_min_rank": history["min_rank"],
                    "historical_score_difference": profile.score - history["min_score"],
                    "plan_count": plan["plan_count"],
                    "subject_requirement": plan["subject_requirement"],
                    "source_ids": sources,
                }
            )
    return accepted, excluded


def verify_portfolio_sql_truth(profile: PortfolioRequest, receipt: dict[str, Any]) -> dict[str, int]:
    """Raise on a dishonest/partial receipt; return *synthetic* rule counts only.

    Result correctness is checked against direct SQLite rows and a separate
    rule implementation. This oracle does not establish real business safety.
    """
    if not isinstance(receipt, dict) or set(receipt) != _KEYS:
        raise UnverifiedPortfolioReceipt("Unexpected portfolio receipt fields")
    expected_request = {
        "province": profile.province,
        "score": profile.score,
        "subject_type": profile.subject_type,
        "year": profile.year,
        "candidates": [list(item) for item in profile.candidates],
    }
    if (
        receipt["data_profile"] != "SYNTHETIC_DEMO"
        or receipt["request"] != expected_request
        or receipt["disclaimer"] != DISCLAIMER
        or receipt["warning"] != _WARNING
        or receipt["human_review_complete"] is not False
        or any(receipt[key] is not None for key in ("admission_probability", "application_ranking", "applicant_rank"))
    ):
        raise UnverifiedPortfolioReceipt("Synthetic-only and manual-review boundaries violated")
    accepted, excluded = _direct_sql(profile)
    if (
        receipt["accepted_historical_comparisons"] != accepted
        or receipt["excluded_candidates"] != excluded
        or receipt["eligible_history_count"] != len(accepted)
        or receipt["excluded_count"] != len(excluded)
        or receipt["evaluated_count"] != len(profile.candidates)
        or receipt["sql_tool_calls"] != 2 * len(profile.candidates) + len({major for _, major in profile.candidates})
        or receipt["status"] != ("historical_candidates_reviewed" if accepted else "needs_review")
    ):
        raise UnverifiedPortfolioReceipt("Portfolio result differs from independent SQL truth")
    return {
        "evaluated": len(profile.candidates),
        "historically_supported": len(accepted),
        "excluded": len(excluded),
    }
