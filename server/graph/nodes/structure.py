"""Structure output node — builds a StructuredPlanningCard from gathered data."""

from __future__ import annotations

from typing import Any

from server.domain.schemas import StructuredPlanningCard

# Threshold for low-score risk warning (450分 ≈ 专科/高职 boundary)
_LOW_SCORE_THRESHOLD = 450
_DEFAULT_SLOT_COUNT = 7
_HIGH_RISK_MAJORS = {"金融", "法学", "新闻", "工商管理", "土木", "建筑学"}
# Slot keys required for next actions
MISSING_SLOT_KEYS = ["province", "score", "subject", "interest", "goal"]

_TITLE_MAP = {
    "gaokao": "高考志愿规划建议",
    "kaoyan": "考研规划建议",
    "career": "职业发展建议",
    "general": "教育规划建议",
}


def structure_output_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build a StructuredPlanningCard from reasoning and data query results.

    Extracts facts, suggestions, risks, and next_actions from the state,
    producing a structured JSON payload for the frontend.
    Falls back to a minimal card if extraction fails.
    """
    scene = state.get("scene", "general")
    slots = state.get("slots", {})
    data = state.get("data_query_results", {})
    reasoning = state.get("reasoning", "")

    try:
        facts = _extract_facts(slots, data, reasoning)
        suggestions = _extract_suggestions(scene, data, reasoning)
        risks = _extract_risks(scene, data, slots)
        next_actions = _extract_next_actions(scene, slots, data)
        sources = _extract_sources(data, state.get("rag_sources", []))
        summary = _build_summary(scene, slots, data)

        filled_count = len([v for v in slots.values() if v])
        total_slots = max(len(slots) if slots else _DEFAULT_SLOT_COUNT, 1)
        confidence = round(filled_count / total_slots, 2)

        card = StructuredPlanningCard(
            title=_TITLE_MAP.get(scene, "教育规划建议"),
            summary=summary,
            scene=scene,
            facts=facts,
            suggestions=suggestions,
            risks=risks,
            next_actions=next_actions,
            sources=sources,
            confidence=confidence,
        )
        structured = card.model_dump()
    except Exception:
        # Graceful fallback: minimal card
        structured = StructuredPlanningCard(
            title=_TITLE_MAP.get(scene, "教育规划建议"),
            summary="数据不足，建议补充更多信息。",
            scene=scene,
        ).model_dump()

    trace = list(state.get("trace", []))
    trace.append({"node": "structure_output", "event": "card_built"})
    return {"structured_result": structured, "trace": trace}


def _extract_facts(slots: dict, data: dict, reasoning: str) -> list[str]:
    """Extract factual statements from slots, data, and reasoning."""
    facts: list[str] = []

    slot_labels = {
        "province": "省份",
        "score": "分数",
        "subject": "选科",
        "interest": "专业意向",
        "region": "地域偏好",
        "family": "家庭背景",
        "goal": "核心诉求",
    }
    for key, label in slot_labels.items():
        val = slots.get(key, "")
        if val:
            facts.append(f"{label}：{val}")

    match_schools = data.get("match_schools", [])
    if match_schools:
        names = [s.get("school_name", s.get("name", "")) for s in match_schools[:5]]
        names = [n for n in names if n]
        if names:
            facts.append(f"匹配院校：{', '.join(names)}")

    rank_info = data.get("rank_info")
    if isinstance(rank_info, dict):
        rank = rank_info.get("rank")
        if isinstance(rank, (int, float)) and not isinstance(rank, bool) and 0 <= rank <= 100_000_000:
            fact = f"位次参考：约{int(rank):,}"
            confidence = rank_info.get("confidence")
            if isinstance(confidence, str) and confidence.strip():
                fact += f"（置信度：{confidence.strip()[:40]}）"
            facts.append(fact)

    major_info = data.get("major_info")
    if major_info and isinstance(major_info, dict):
        name = major_info.get("name", "")
        emp = major_info.get("employment_rate")
        if name:
            emp_str = f"，就业率{emp * 100:.0f}%" if emp else ""
            facts.append(f"专业信息：{name}{emp_str}")

    return facts


def _extract_suggestions(scene: str, data: dict, reasoning: str) -> list[str]:
    """Extract actionable suggestions from data and reasoning."""
    suggestions: list[str] = []

    match_schools = data.get("match_schools", [])
    if match_schools and scene == "gaokao":
        for s in match_schools[:3]:
            name = s.get("school_name", s.get("name", ""))
            score = s.get("min_score", s.get("score_line", ""))
            level = s.get("school_level", "")
            if name:
                tag = f"（{level}）" if level else ""
                score_str = f"，参考线{score}分" if score else ""
                suggestions.append(f"推荐：{name}{tag}{score_str}")

    if not suggestions:
        suggestions.append("建议补充更多信息以获得精准推荐")

    return suggestions


def _extract_sources(data: dict, rag_sources: list[dict] | None) -> list[str]:
    """Collect traceable origins for the facts in this card."""
    sources: list[str] = []
    for school in data.get("match_schools", [])[:5]:
        source = school.get("data_source")
        if source and source not in sources:
            sources.append(str(source))
    rank = data.get("rank_info")
    if isinstance(rank, dict):
        source = rank.get("source")
        if source and source not in sources:
            sources.append(str(source))
    for citation in rag_sources or []:
        if isinstance(citation, dict):
            source = citation.get("source") or citation.get("section_title")
            if source and source not in sources:
                sources.append(str(source))
    if not sources:
        sources.append("SYNTHETIC_DEMO 合成演示数据（无真实招生来源）")
    return sources


def _extract_risks(scene: str, data: dict, slots: dict) -> list[str]:
    """Extract risk warnings."""
    risks: list[str] = []

    if scene == "gaokao":
        score = slots.get("score", "")
        if score:
            try:
                score_num = int(str(score).replace("分", ""))
                if score_num < _LOW_SCORE_THRESHOLD:
                    risks.append("分数较低，建议重点关注专科/高职优质专业")
            except (ValueError, TypeError):
                pass

        interest = slots.get("interest", "")
        for r in _HIGH_RISK_MAJORS:
            if r in interest:
                risks.append(f"「{interest}」属于需谨慎选择的专业方向，建议关注就业数据")
                break

    if not risks:
        risks.append("数据有限，建议以官方最新信息为准")

    return risks


def _extract_next_actions(scene: str, slots: dict, data: dict) -> list[str]:
    """Extract concrete next steps."""
    actions: list[str] = []

    missing = [k for k in MISSING_SLOT_KEYS if not slots.get(k)]
    if missing:
        labels = {"province": "省份", "score": "分数", "subject": "选科", "interest": "专业意向", "goal": "核心诉求"}
        action_text = "、".join(labels.get(m, m) for m in missing[:3])
        actions.append(f"补充{action_text}信息")

    if data.get("match_schools"):
        actions.append("对比推荐院校的录取数据和招生计划")
        actions.append("到省考试院官网核实最新录取信息")

    return actions


def _build_summary(scene: str, slots: dict, data: dict) -> str:
    """Build a one-line summary of the current analysis."""
    parts: list[str] = []
    province = slots.get("province", "")
    score = slots.get("score", "")
    interest = slots.get("interest", "")

    if province:
        parts.append(province)
    if score:
        parts.append(str(score))
    if interest:
        parts.append(f"意向{interest}")

    if parts:
        return "，".join(parts) + "，规划分析中。"
    return "等待更多画像信息以生成精准规划。"
