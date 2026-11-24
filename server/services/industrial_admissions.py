"""Bounded historical SQL evidence, not an eligibility or admission prediction."""

from __future__ import annotations

import os
import re

from sqlalchemy import func, or_

from db.models import AdmissionScore, School, YiFenYiDuan
from server.domain.schemas import StructuredPlanningCard

_COHORTS = (
    ("物理", "物理类"),
    ("历史", "历史类"),
    ("理科", "理工"),
    ("文科", "文史"),
    ("综合", "3+3综合"),
)


def approved_year() -> int:
    raw = os.environ.get("GAOBAO_ADMISSIONS_YEAR", "")
    if not re.fullmatch(r"20[0-9]{2}", raw):
        raise RuntimeError("Explicit approved admissions year required")
    return int(raw)


def query_history(db, *, province: str, score: int, subject: str, year: int) -> dict:
    """Use no nearest-score, other-year, other-subject or inferred-rank fallback."""
    base = {"year": year, "province": province, "score": score, "subject": subject}
    if not 100 <= score <= 750 or not province.strip():
        return {**base, "status": "INVALID_SCOPE"}
    aliases = next((group for group in _COHORTS if subject in group), ())
    if not aliases:
        return {**base, "status": "UNSUPPORTED_COHORT"}
    ranks = (
        db.query(YiFenYiDuan)
        .filter(
            YiFenYiDuan.province == province,
            YiFenYiDuan.year == year,
            YiFenYiDuan.subject_type.in_(aliases),
            YiFenYiDuan.score == score,
        )
        .order_by(YiFenYiDuan.id)
        .limit(2)
        .all()
    )
    if len(ranks) != 1 or not ranks[0].cumulative_count or ranks[0].cumulative_count < 1:
        return {**base, "status": "NO_UNAMBIGUOUS_RANK"}
    rank = ranks[0]
    rows = (
        db.query(AdmissionScore, School.name)
        .join(School, AdmissionScore.school_id == School.id)
        .filter(
            AdmissionScore.province == province,
            AdmissionScore.year == year,
            AdmissionScore.subject_type == rank.subject_type,
            AdmissionScore.major_id.is_(None),
            or_(School.special_type.is_(None), School.special_type != "SYNTHETIC_DEMO_DATA"),
            AdmissionScore.min_rank >= max(1, rank.cumulative_count * 95 // 100),
            AdmissionScore.min_rank <= rank.cumulative_count * 105 // 100,
            AdmissionScore.min_score.between(0, 750),
            AdmissionScore.batch.isnot(None),
        )
        .order_by(func.abs(AdmissionScore.min_rank - rank.cumulative_count), AdmissionScore.id)
        .limit(10)
        .all()
    )
    return {
        **base,
        "status": "HISTORICAL_ROWS" if rows else "NO_SCHOOL_ROWS",
        "subject": rank.subject_type,
        "rank": rank.cumulative_count,
        "rank_id": rank.id,
        "rows": [
            {
                "id": row.id,
                "school": name,
                "batch": row.batch,
                "min_score": row.min_score,
                "min_rank": row.min_rank,
            }
            for row, name in rows
        ],
    }


def build_history_reply(state: dict) -> tuple[str, dict]:
    """Render only exact SQL facts. No heuristic recommendation or confidence."""
    evidence = (state.get("data_query_results") or {}).get("industrial_history") or {}
    year = approved_year()
    title = f"{year}年招生历史数据查询"
    facts: list[str] = []
    sources: list[str] = []
    if state.get("knowledge_query"):
        summary = "现行政策知识库尚未获准发布，请核实省教育考试院最新公告。"
    elif state.get("scene") != "gaokao":
        summary = "当前发布范围仅为高考历史招生数据查询。"
    elif evidence.get("status") == "YEAR_MISMATCH":
        summary = f"当前发布只支持{year}年历史数据，不能回答其他年份。"
    elif state.get("missing_fields") or evidence.get("status") == "UNSUPPORTED_COHORT":
        summary = "请补充省份、明确分数和招生科类（物理类、历史类、理科、文科或综合）。"
    elif evidence.get("status") in {"HISTORICAL_ROWS", "NO_SCHOOL_ROWS"}:
        cohort = f"{year}年{evidence['province']}{evidence['subject']}"
        summary = f"{cohort}，同年同科类历史记录；不代表当年或未来录取资格。"
        facts.append(f"{evidence['score']}分对应历史累计位次{evidence['rank']}（一分一段记录 #{evidence['rank_id']}）")
        sources.append(f"yi_fen_yi_duan:{evidence['rank_id']}")
        sources.append(f"dataset_authorization_ref:{os.environ.get('GAOBAO_DATA_APPROVAL_REF', 'unavailable')}")
        for row in evidence["rows"]:
            facts.append(
                f"{row['school']}｜{row['batch']}｜历史最低分{row['min_score']}分｜"
                f"最低位次{row['min_rank']}（录取记录 #{row['id']}）"
            )
            sources.append(f"admission_scores:{row['id']}")
        if not evidence["rows"]:
            summary += " 未找到位次邻域内的院校级记录。"
    else:
        summary = "没有唯一、同年同省同科类的精确位次依据，无法提供匹配结果。"
    risks = ["历史记录不是录取预测；专业、选科组合、批次资格及当年计划须以官方公告核实。"]
    card = StructuredPlanningCard(
        title=title,
        scene="gaokao",
        summary=summary,
        facts=facts,
        sources=sources,
        risks=risks,
        next_actions=["核对省教育考试院及院校当年招生简章。"],
        confidence=0.0,
    ).model_dump()
    return "\n".join([title, summary, *facts, *risks]), card
