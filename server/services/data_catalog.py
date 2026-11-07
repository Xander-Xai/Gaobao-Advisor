"""Read-only data catalog for the public explorer.

The administrator workbench has its own repository with review/audit fields.
This service exposes the smaller public read model: aggregate coverage,
provenance, school browsing, and subject-type-separated score trends.

It owns SQL for the public data API so the router stays a transport layer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from db.models import (
    AdmissionScore,
    DatasetSnapshot,
    EnrollmentPlan,
    Major,
    School,
    SubjectRanking,
    YiFenYiDuan,
)
from server.services.data_profile import detect_data_profile

SYNTHETIC_MARKER = "SYNTHETIC_DEMO_DATA"

_PROFILE_NOTICES = {
    "SYNTHETIC_DEMO": (
        "当前展示为程序生成的 SYNTHETIC_DEMO 演示数据，不对应任何真实院校、专业或录取结果，不可用于真实志愿决策。"
    ),
    "EMPTY": "当前数据库为空。运行演示数据初始化命令后即可浏览。",
    "CUSTOM": "当前数据库为部署者导入的自有数据；来源、授权与准确性由部署者负责。",
    "MIXED": "当前数据库同时包含合成演示数据与部署者导入的数据，请在具体记录上核对来源标识。",
    "UNKNOWN": "数据来源无法确认（院校表不可查询）；不以合成数据展示。",
}

TREND_NOTICE = (
    "趋势仅在同一学校、同一省份、同一科类内聚合；不同省份、考试模式、"
    "科类或专业组的口径不可直接混合。演示数据不代表未来录取结果。"
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _distinct_strings(session: Session, column) -> list[str]:
    rows = session.execute(select(column).where(column.isnot(None)).distinct().order_by(column)).scalars().all()
    return [str(value) for value in rows if str(value).strip()]


def _snapshot_dict(snapshot: DatasetSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "dataset_version": snapshot.dataset_version,
        "profile": snapshot.profile,
        "source_note": snapshot.source_note,
        "generated_at": snapshot.generated_at,
        "year_from": snapshot.year_from,
        "year_to": snapshot.year_to,
        "seed": snapshot.seed,
        "is_synthetic": bool(snapshot.is_synthetic),
    }


def get_overview(session: Session) -> dict[str, Any]:
    """Return coverage, provenance and per-year score counts.

    Aggregate counts are batched into a single scalar-subquery statement and
    the per-year GROUP BY query supplies both ``years`` and ``scores_by_year``,
    reducing sequential round-trips (16 → 9) without changing the response
    contract (same keys, types and ordering). This mitigates concurrency
    contention where each request previously held a DB session across ~16
    sequential queries; see Issue #91 for measured before/after.
    """
    data_profile = detect_data_profile(session)
    scores_by_year = [
        {"year": int(year), "count": int(count)}
        for year, count in session.execute(
            select(AdmissionScore.year, func.count(AdmissionScore.id))
            .where(AdmissionScore.year.isnot(None))
            .group_by(AdmissionScore.year)
            .order_by(AdmissionScore.year)
        ).all()
    ]
    years = [row["year"] for row in scores_by_year]
    counts_row = session.execute(
        select(
            select(func.count(School.id)).scalar_subquery().label("schools"),
            select(func.count(Major.id)).scalar_subquery().label("majors"),
            select(func.count(AdmissionScore.id)).scalar_subquery().label("admission_scores"),
            select(func.count(EnrollmentPlan.id)).scalar_subquery().label("enrollment_plans"),
            select(func.count(SubjectRanking.id)).scalar_subquery().label("subject_rankings"),
            select(func.count(YiFenYiDuan.id)).scalar_subquery().label("score_rank_rows"),
            select(func.count(func.distinct(AdmissionScore.school_id))).scalar_subquery().label("schools_with_scores"),
            select(func.count(func.distinct(EnrollmentPlan.school_id))).scalar_subquery().label("schools_with_plans"),
        )
    ).one()
    counts = {
        "schools": int(counts_row.schools),
        "majors": int(counts_row.majors),
        "admission_scores": int(counts_row.admission_scores),
        "enrollment_plans": int(counts_row.enrollment_plans),
        "subject_rankings": int(counts_row.subject_rankings),
        "score_rank_rows": int(counts_row.score_rank_rows),
    }
    snapshot = session.query(DatasetSnapshot).order_by(DatasetSnapshot.id.desc()).first()
    return {
        "data_profile": data_profile,
        "notice": _PROFILE_NOTICES.get(data_profile, _PROFILE_NOTICES["UNKNOWN"]),
        "counts": counts,
        "years": years,
        "year_from": years[0] if years else None,
        "year_to": years[-1] if years else None,
        "provinces": _distinct_strings(session, School.province),
        "levels": _distinct_strings(session, School.level),
        "school_types": _distinct_strings(session, School.school_type),
        "subject_types": _distinct_strings(session, AdmissionScore.subject_type),
        "scores_by_year": scores_by_year,
        "schools_with_scores": int(counts_row.schools_with_scores),
        "schools_with_plans": int(counts_row.schools_with_plans),
        "latest_admission_year": years[-1] if years else None,
        "snapshot": _snapshot_dict(snapshot),
    }


def get_filter_options(session: Session) -> dict[str, list[Any]]:
    years = [
        int(year)
        for year in session.execute(
            select(AdmissionScore.year)
            .where(AdmissionScore.year.isnot(None))
            .distinct()
            .order_by(AdmissionScore.year.desc())
        )
        .scalars()
        .all()
    ]
    majors = [
        str(name)
        for name in session.execute(select(Major.name).distinct().order_by(Major.name)).scalars().all()
        if str(name).strip()
    ]
    return {
        "provinces": _distinct_strings(session, School.province),
        "levels": _distinct_strings(session, School.level),
        "school_types": _distinct_strings(session, School.school_type),
        "years": years,
        "subject_types": _distinct_strings(session, AdmissionScore.subject_type),
        "majors": majors,
    }


def _school_stats_subqueries():
    score_count = (
        select(func.count(AdmissionScore.id))
        .where(AdmissionScore.school_id == School.id)
        .correlate(School)
        .scalar_subquery()
    )
    plan_count = (
        select(func.count(EnrollmentPlan.id))
        .where(EnrollmentPlan.school_id == School.id)
        .correlate(School)
        .scalar_subquery()
    )
    latest_year = (
        select(func.max(AdmissionScore.year))
        .where(AdmissionScore.school_id == School.id)
        .correlate(School)
        .scalar_subquery()
    )
    latest_min = (
        select(func.min(AdmissionScore.min_score))
        .where(
            AdmissionScore.school_id == School.id,
            AdmissionScore.year == latest_year,
        )
        .correlate(School)
        .scalar_subquery()
    )
    return score_count, plan_count, latest_year, latest_min


def _school_filters(
    q: str | None, province: str | None, level: str | None, school_type: str | None, school_id: int | None
):
    filters = []
    if q:
        like = f"%{_escape_like(q)}%"
        filters.append(or_(School.name.like(like, escape="\\"), School.city.like(like, escape="\\")))
    if province:
        filters.append(School.province == province)
    if level:
        filters.append(School.level == level)
    if school_type:
        filters.append(School.school_type == school_type)
    if school_id is not None:
        filters.append(School.id == school_id)
    return filters


def list_schools(
    session: Session,
    *,
    q: str | None = None,
    province: str | None = None,
    level: str | None = None,
    school_type: str | None = None,
    school_id: int | None = None,
    limit: int = 20,
    cursor: int | None = None,
) -> dict[str, Any]:
    score_count, plan_count, latest_year, latest_min = _school_stats_subqueries()
    query = select(
        School.id,
        School.name,
        School.province,
        School.city,
        School.level,
        School.school_type,
        School.ranking,
        School.is_985,
        School.is_211,
        School.is_double_first_class,
        School.special_type,
        School.data_source_note,
        score_count.label("score_count"),
        plan_count.label("plan_count"),
        latest_year.label("latest_year"),
        latest_min.label("latest_min_score"),
    )
    count_query = select(func.count(School.id))
    filters = _school_filters(q, province, level, school_type, school_id)
    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)
    if cursor is not None:
        query = query.where(School.id > cursor)
    query = query.order_by(School.id.asc()).limit(limit + 1)
    rows = [dict(row._mapping) for row in session.execute(query).all()]
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    next_cursor = str(rows[-1]["id"]) if rows and has_more else None
    total = int(session.execute(count_query).scalar_one())
    for row in rows:
        row["is_985"] = bool(row["is_985"])
        row["is_211"] = bool(row["is_211"])
        row["is_double_first_class"] = bool(row["is_double_first_class"])
        row["score_count"] = int(row["score_count"] or 0)
        row["plan_count"] = int(row["plan_count"] or 0)
    return {
        "items": rows,
        "total": total,
        "count": len(rows),
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _score_rows(session: Session, school_id: int, *, major: str | None = None) -> list[AdmissionScore]:
    query = (
        session.query(AdmissionScore)
        .options(joinedload(AdmissionScore.major))
        .filter(AdmissionScore.school_id == school_id)
    )
    if major:
        query = query.join(Major, AdmissionScore.major_id == Major.id).filter(Major.name == major)
    return (
        query.order_by(
            AdmissionScore.year.desc(),
            AdmissionScore.subject_type.asc(),
            AdmissionScore.min_score.asc(),
        )
        .limit(200)
        .all()
    )


def get_school_detail(session: Session, school_id: int) -> dict[str, Any] | None:
    listing = list_schools(session, school_id=school_id, limit=1)
    if not listing["items"]:
        return None
    item = listing["items"][0]
    school = session.get(School, school_id)
    if school is None:
        return None
    scores = _score_rows(session, school_id)
    plans = (
        session.query(EnrollmentPlan)
        .options(joinedload(EnrollmentPlan.major))
        .filter(EnrollmentPlan.school_id == school_id)
        .order_by(EnrollmentPlan.year.desc(), EnrollmentPlan.id.asc())
        .limit(200)
        .all()
    )
    item.update(
        {
            "website": school.website,
            "description": school.description,
            "scores": [
                {
                    "id": row.id,
                    "major_name": row.major.name if row.major else None,
                    "province": row.province,
                    "year": row.year,
                    "batch": row.batch,
                    "subject_type": row.subject_type,
                    "min_score": row.min_score,
                    "avg_score": row.avg_score,
                    "max_score": row.max_score,
                    "min_rank": row.min_rank,
                    "plan_count": row.plan_count,
                }
                for row in scores
            ],
            "plans": [
                {
                    "id": row.id,
                    "major_name": row.major.name if row.major else None,
                    "province": row.province,
                    "year": row.year,
                    "plan_count": row.plan_count,
                    "subject_requirement": row.subject_requirement,
                    "batch": row.batch,
                    "duration": row.duration,
                    "tuition": row.tuition,
                }
                for row in plans
            ],
            "data_availability": {
                "has_scores": bool(scores),
                "has_plans": bool(plans),
                "available_years": sorted({row.year for row in scores}),
                "subject_types": sorted({row.subject_type for row in scores}),
            },
        }
    )
    return item


def _direction(points: list[dict[str, Any]]) -> tuple[str, float | None]:
    scores = [point["min_score"] for point in points if point["min_score"] is not None]
    if len(scores) < 2:
        return "数据不足", None
    change = float(scores[-1] - scores[0])
    diffs = [scores[index + 1] - scores[index] for index in range(len(scores) - 1)]
    if all(diff > 0 for diff in diffs):
        return "逐年上升", change
    if all(diff < 0 for diff in diffs):
        return "逐年下降", change
    return "波动", change


def get_trend(
    session: Session,
    *,
    school_name: str,
    province: str,
    subject_type: str | None = None,
    major: str | None = None,
    years: int = 3,
) -> dict[str, Any] | None:
    school = session.query(School).filter(School.name == school_name).first()
    if school is None:
        return None

    base = session.query(AdmissionScore).filter(
        AdmissionScore.school_id == school.id,
        AdmissionScore.province == province,
        AdmissionScore.min_score.isnot(None),
    )
    if subject_type:
        base = base.filter(AdmissionScore.subject_type == subject_type)
    if major:
        base = base.join(Major, AdmissionScore.major_id == Major.id).filter(Major.name == major)
    all_rows = base.all()
    if not all_rows:
        latest = None
        expected_years: list[int] = []
    else:
        latest = max(row.year for row in all_rows)
        expected_years = list(range(latest - max(1, years) + 1, latest + 1))
        all_rows = [row for row in all_rows if row.year in expected_years]

    series: list[dict[str, Any]] = []
    for current_subject in sorted({row.subject_type for row in all_rows}):
        rows = [row for row in all_rows if row.subject_type == current_subject]
        grouped: dict[int, list[AdmissionScore]] = {}
        for row in rows:
            grouped.setdefault(row.year, []).append(row)
        points = []
        for year in sorted(grouped):
            year_rows = grouped[year]
            min_scores = [row.min_score for row in year_rows if row.min_score is not None]
            avg_scores = [row.avg_score for row in year_rows if row.avg_score is not None]
            max_scores = [row.max_score for row in year_rows if row.max_score is not None]
            ranks = [row.min_rank for row in year_rows if row.min_rank is not None]
            points.append(
                {
                    "year": year,
                    "min_score": min(min_scores) if min_scores else None,
                    "avg_score": round(sum(avg_scores) / len(avg_scores), 1) if avg_scores else None,
                    "max_score": max(max_scores) if max_scores else None,
                    "min_rank": min(ranks) if ranks else None,
                    "record_count": len(year_rows),
                }
            )
        direction, change = _direction(points)
        series.append({"subject_type": current_subject, "points": points, "direction": direction, "change": change})

    covered_years = {point["year"] for item in series for point in item["points"]}
    missing_years = [year for year in expected_years if year not in covered_years]
    return {
        "school_id": school.id,
        "school_name": school.name,
        "province": province,
        "scope": "major" if major else "school",
        "major": major,
        "aggregation": "major_min_score" if major else "school_lowest_score_across_majors",
        "series": series,
        "missing_years": missing_years,
        "notice": TREND_NOTICE,
    }


def search_scores(
    session: Session,
    *,
    school_name: str,
    province: str,
    year: int | None = None,
    major: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> dict[str, Any]:
    school = session.query(School).filter(School.name == school_name).first()
    if school is None:
        return {"items": [], "next_cursor": None, "has_more": False}
    query = session.query(AdmissionScore).filter(
        AdmissionScore.school_id == school.id,
        AdmissionScore.province == province,
    )
    if year:
        query = query.filter(AdmissionScore.year == year)
    if major:
        query = query.join(Major, AdmissionScore.major_id == Major.id).filter(Major.name == major)
    if cursor is not None:
        query = query.filter(AdmissionScore.id > cursor)
    rows = query.order_by(AdmissionScore.id).limit(limit + 1).all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    results = []
    for row in rows:
        item = {
            "id": row.id,
            "school_name": school_name,
            "province": row.province,
            "year": row.year,
            "batch": row.batch,
            "subject_type": row.subject_type,
            "min_score": row.min_score,
            "avg_score": row.avg_score,
            "max_score": row.max_score,
            "min_rank": row.min_rank,
        }
        # Preserve the original contract: school-level rows have no `major`
        # key, rather than a `major: null` that changes shape by row.
        if row.major is not None:
            item["major"] = row.major.name
        results.append(item)
    return {
        "items": results,
        "next_cursor": str(rows[-1].id) if rows and has_more else None,
        "has_more": has_more,
    }


def search_plans(
    session: Session,
    *,
    school_name: str,
    province: str | None = None,
    year: int | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> dict[str, Any]:
    school = session.query(School).filter(School.name == school_name).first()
    if school is None:
        return {"items": [], "next_cursor": None, "has_more": False}
    query = session.query(EnrollmentPlan).filter(EnrollmentPlan.school_id == school.id)
    if province:
        query = query.filter(EnrollmentPlan.province == province)
    if year:
        query = query.filter(EnrollmentPlan.year == year)
    if cursor is not None:
        query = query.filter(EnrollmentPlan.id > cursor)
    rows = query.order_by(EnrollmentPlan.id).limit(limit + 1).all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    results = []
    for row in rows:
        item = {
            "id": row.id,
            "school_name": school_name,
            "province": row.province,
            "year": row.year,
            "plan_count": row.plan_count,
            "subject_requirement": row.subject_requirement,
            "batch": row.batch,
            "duration": row.duration,
            "tuition": row.tuition,
        }
        if row.major is not None:
            item["major"] = row.major.name
        results.append(item)
    return {
        "items": results,
        "next_cursor": str(rows[-1].id) if rows and has_more else None,
        "has_more": has_more,
    }
