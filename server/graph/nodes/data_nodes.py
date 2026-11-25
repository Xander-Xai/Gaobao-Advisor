"""Data query node — fetches admission and school data."""

from __future__ import annotations

import re
from typing import Any

from server.services import data_query as dq


def numeric_score(slots: dict[str, Any]) -> int | None:
    """Resolve a numeric score from either ``score`` or ``score_rank``.

    Slot extraction stores the user-facing value as ``"600分"`` (and may append
    a rank, e.g. ``"600分 / 位次3000"``), while the data layer needs an integer.
    A rank-only value such as ``"位次3000"`` is deliberately not treated as a
    score.
    """
    for key in ("score", "score_rank"):
        value = slots.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            score = int(value)
            return score if 100 <= score <= 750 else None
        text = str(value).strip()
        if not text:
            continue
        if "分" not in text and not text.isdigit():
            continue
        match = re.search(r"\d{2,3}", text)
        if match:
            score = int(match.group(0))
            if 100 <= score <= 750:
                return score
    return None


def data_query_node(state: dict[str, Any]) -> dict[str, Any]:
    """Query relevant admission data based on filled slots.

    For the gaokao scene, queries matching schools and score data.
    Handles errors gracefully — data query failures should not block the pipeline.
    """
    from server.industrial_runtime import industrial_enabled

    scene = state.get("scene", "general")
    slots = state.get("slots", {})
    if industrial_enabled():
        from db.database import get_session
        from server.services.industrial_admissions import approved_year, exact_score, query_history

        year = approved_year()
        history = {}
        requested = set(re.findall(r"(20[0-9]{2})\s*年", state.get("input_text", "")))
        if requested - {str(year)}:
            history = {"status": "YEAR_MISMATCH"}
        elif scene == "gaokao" and not state.get("knowledge_query") and not state.get("industrial_score_invalid"):
            score = exact_score(slots)
            province, subject = slots.get("province"), slots.get("subject")
            if score is not None and isinstance(province, str) and isinstance(subject, str):
                db = get_session()
                try:
                    history = query_history(db, province=province, score=score, subject=subject, year=year)
                finally:
                    db.close()
        return {
            "data_query_results": {"industrial_history": history},
            "trace": list(state.get("trace", [])) + [{"node": "data_query", "event": "industrial_exact_history"}],
        }
    results: dict[str, Any] = {}

    try:
        if scene == "gaokao":
            province = slots.get("province")
            score = numeric_score(slots)
            subject = slots.get("subject")
            interest = slots.get("interest")

            if province and score and subject:
                # Normalize subject for query
                subject_type = (
                    "物理" if subject in ("理科", "物理") else "历史" if subject in ("文科", "历史") else subject
                )

                # Match schools
                results["match_schools"] = dq.query_match_schools_v2(
                    score=score,
                    province=province,
                    subject_type=subject_type,
                    strategy="稳",
                )

                # One-score-one-rank
                yfyd = dq.query_yi_fen_yi_duan(province, score, subject_type)
                if yfyd:
                    results["rank_info"] = yfyd

            # School by major
            if interest and province and score:
                subject_type = (
                    "物理" if subject in ("理科", "物理") else "历史" if subject in ("文科", "历史") else "物理"
                )
                results["schools_by_major"] = dq.query_schools_by_major(
                    major_name=interest,
                    province=province,
                    score=score,
                    subject_type=subject_type,
                )

            # Major info
            if interest:
                major_info = dq.query_major_info(interest)
                if major_info:
                    results["major_info"] = major_info

        elif scene == "kaoyan":
            interest = slots.get("interest")
            if interest:
                major_info = dq.query_major_info(interest)
                if major_info:
                    results["major_info"] = major_info

    except Exception as exc:
        results["error"] = str(exc)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "data_query",
            "event": f"keys={list(results.keys())}",
        }
    )

    return {"data_query_results": results, "trace": trace}
