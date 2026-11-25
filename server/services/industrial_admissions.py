"""Bounded historical SQL evidence, not an eligibility or admission prediction."""

from __future__ import annotations

import hashlib
import json
import os
import re

from sqlalchemy import func, or_

from db.models import AdmissionScore, School, YiFenYiDuan
from server.domain.schemas import StructuredPlanningCard
from server.services.admissions_lineage import ManifestError, approved_manifest, verified_lineage

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


def exact_score(slots: dict) -> int | None:
    """Do not reinterpret rank, score deltas or truncated numbers as a score."""
    scores = []
    for key in ("score", "score_rank"):
        value = slots.get(key)
        if value is None or value == "":
            continue
        if key == "score" and type(value) is int:
            score = value
        elif isinstance(value, str):
            if key == "score":
                pattern = r"([0-9]{2,3})\s*分?"
            else:
                pattern = r"([0-9]{2,3})\s*分(?:\s*/\s*位次[0-9,]+)?"
            match = re.fullmatch(pattern, value.strip())
            if not match:
                return None
            score = int(match.group(1))
        else:
            return None
        if not 100 <= score <= 750:
            return None
        scores.append(score)
    return scores[0] if scores and len(set(scores)) == 1 else None


def explicit_turn_score(text: str) -> tuple[bool, int | None]:
    """Recognize explicit numeric scores without the legacy substring parser.

    The boolean distinguishes a score mention from a turn with no score.
    Relative, approximate and multiple scores require clarification.
    """
    matches = re.findall(r"[+＋\-－−]?[0-9０-９]+(?:[.,，．][0-9０-９]+)*\s*分", text)
    if not matches:
        return False, None
    if len(matches) != 1 or re.search(
        r"不到|不满|没到|低于|高于|差|超过|超了|过了|线上|线下|左右|大约|约|上下|至少|至多|以上|以下|分多|多分",
        text,
    ):
        return True, None
    match = re.fullmatch(r"([0-9]{3})\s*分", matches[0])
    if match is None:
        return True, None
    score = int(match.group(1))
    return True, score if 100 <= score <= 750 else None


def evidence_digest(evidence: dict) -> str:
    """Fingerprint delivered values; not a signature or proof of official origin."""
    payload = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def query_history(db, *, province: str, score: int, subject: str, year: int, manifest=None) -> dict:
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
    evidence = {
        **base,
        "status": "HISTORICAL_ROWS" if rows else "NO_SCHOOL_ROWS",
        "subject": rank.subject_type,
        "rank": rank.cumulative_count,
        "rank_id": rank.id,
        "rows": [
            {
                "id": row.id,
                "school": name,
                "school_id": row.school_id,
                "batch": row.batch,
                "min_score": row.min_score,
                "min_rank": row.min_rank,
            }
            for row, name in rows
        ],
    }
    if manifest is not None:
        try:
            evidence["lineage"] = verified_lineage(evidence, manifest)
        except ManifestError:
            return {**base, "status": "UNAPPROVED_LINEAGE"}
    evidence["evidence_sha256"] = evidence_digest(evidence)
    return evidence


def _lineage_valid(evidence: dict, year: int) -> bool:
    from server.industrial_runtime import industrial_enabled

    if not industrial_enabled() and "lineage" not in evidence:
        return True  # Reference SQL helper tests, not the industrial route.
    try:
        return evidence.get("lineage") == verified_lineage(evidence, approved_manifest(year))
    except (ManifestError, KeyError, TypeError, ValueError):
        return False


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
    elif state.get("industrial_score_invalid"):
        summary = "请提供一个明确的高考整数总分（例如600分），不使用分差、近似值或多个分数。"
    elif evidence.get("status") == "YEAR_MISMATCH":
        summary = f"当前发布只支持{year}年历史数据，不能回答其他年份。"
    elif state.get("missing_fields") or evidence.get("status") == "UNSUPPORTED_COHORT":
        summary = "请补充省份、明确分数和招生科类（物理类、历史类、理科、文科或综合）。"
    elif evidence.get("status") == "UNAPPROVED_LINEAGE":
        summary = "历史招生数据缺少已审核的来源或发生变化，已停止发布。"
    elif evidence.get("status") in {"HISTORICAL_ROWS", "NO_SCHOOL_ROWS"} and (
        evidence.get("year") != year
        or evidence.get("evidence_sha256") != evidence_digest(evidence)
        or not _lineage_valid(evidence, year)
    ):
        summary = "历史数据证据不一致，已停止发布，请稍后重试。"
    elif evidence.get("status") in {"HISTORICAL_ROWS", "NO_SCHOOL_ROWS"}:
        cohort = f"{year}年{evidence['province']}{evidence['subject']}"
        summary = f"{cohort}，同年同科类历史记录；不代表当年或未来录取资格。"
        facts.append(f"{evidence['score']}分对应历史累计位次{evidence['rank']}（一分一段记录 #{evidence['rank_id']}）")
        sources.append(f"yi_fen_yi_duan:{evidence['rank_id']}")
        digest = evidence["evidence_sha256"]
        sources.append(f"historical_sql_sha256:{digest}")
        facts.append(f"查询结果指纹：{digest}（仅标识本次数据库事实，不证明官方来源）")
        lineage = evidence.get("lineage")
        if lineage:
            facts.append(f"审核数据版本：{lineage['dataset_version']}；清单指纹：{lineage['manifest_sha256']}")
            sources.append(f"admissions_manifest_sha256:{lineage['manifest_sha256']}")
            for record in lineage["records"]:
                # Public reviewed URL and row locator, not internal approval identifiers.
                citation = f"{record['row']}｜{record['url']}｜{record['locator']}｜源文件SHA-256:{record['file_sha256']}"
                sources.append(citation)
                facts.append(f"来源登记：{citation}")
        for row in evidence["rows"]:
            facts.append(
                f"{row['school']}｜{row['batch']}｜历史最低分{row['min_score']}分｜"
                f"最低位次{row['min_rank']}（录取记录 #{row['id']}）"
            )
            sources.append(f"admission_scores:{row['id']}")
        if not evidence["rows"]:
            summary += " 本次数据库检索未找到位次邻域内的院校级记录，不代表官方不存在记录。"
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
