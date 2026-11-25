"""Render reply node — builds the final text response."""

from __future__ import annotations

from typing import Any

# Disclaimer suffix appended to all advice
_DISCLAIMER = (
    "\n\n---\n声明：以上分析基于当前数据源和AI模型，仅供参考。"
    "本仓库默认使用 SYNTHETIC_DEMO 合成演示数据，不对应真实院校或录取结果，"
    "不可用于真实志愿决策；最终志愿填报请以各省教育考试院官方发布信息为准。"
)


def render_reply_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build the final reply text for the user.

    If a reply is already set (e.g. from question_generate or
    security_scan), pass it through unchanged.  Otherwise, assemble
    a reply from the structured result and reasoning.
    """
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        from server.services.industrial_admissions import build_history_reply

        reply, card = build_history_reply(state)
        return {"reply": reply, "template_reply": reply, "reply_source": "graph", "structured_result": card}

    # 如果 reply 已有,追加 disclaimer(来源标注由 source_attribution 节点处理)
    existing_reply = state.get("reply", "")
    if existing_reply:
        if "声明：以上分析基于" not in existing_reply:
            final_reply = existing_reply + _DISCLAIMER
        else:
            final_reply = existing_reply
        trace = list(state.get("trace", []))
        trace.append({"node": "render_reply", "event": "llm_reply_with_disclaimer"})
        return {"reply": final_reply, "trace": trace, "reply_source": "graph"}

    scene = state.get("scene", "general")
    slots = state.get("slots", {})
    structured = state.get("structured_result", {})
    data = state.get("data_query_results", {}) or {}
    reasoning = state.get("reasoning", "")

    parts = []

    if scene == "gaokao":
        # Build gaokao-specific reply
        province = slots.get("province", "")
        score = slots.get("score") or slots.get("score_rank", "")
        subject = slots.get("subject", "")
        interest = slots.get("interest", "")

        score_text = str(score)
        if score_text and not score_text.endswith("分"):
            score_text = f"{score_text}分"

        header = f"根据您提供的信息（{province}考生，{subject}，{score_text}"
        if interest:
            header += f"，意向专业：{interest}"
        header += "），我为您分析如下："
        parts.append(header)

        # ``structure_output`` produces facts/suggestions; the raw matched
        # schools stay in data_query_results. Read both so the offline
        # template is as useful as the structured card, not just a header.
        matched = data.get("match_schools") or structured.get("matched_schools") or []
        if matched:
            parts.append("\n推荐院校（来自合成招生数据）：")
            for i, school in enumerate(matched[:5], 1):
                name = school.get("school_name") or school.get("name", "")
                line = school.get("min_score") or school.get("score_line") or ""
                level = school.get("school_level") or ""
                level_text = f"，{level}" if level else ""
                line_text = f"，参考线{line}分" if line else ""
                parts.append(f"{i}. {name}{level_text}{line_text}")

        rank = data.get("rank_info") or structured.get("rank_analysis")
        if rank:
            rank_text = rank.get("rank") if isinstance(rank, dict) else rank
            if rank_text:
                parts.append(f"\n分数位次分析：位次约{rank_text}")

        major = data.get("major_info") or structured.get("major_analysis")
        if major and isinstance(major, dict):
            parts.append(f"\n专业信息：{major.get('description', str(major)[:200])}")

        sources = structured.get("sources") or []
        if sources:
            parts.append("\n数据与知识来源：")
            for source in sources[:5]:
                parts.append(f"- {source}")

    elif scene == "kaoyan":
        interest = slots.get("interest", "")
        parts.append(f"关于{interest or '考研'}方向，以下是我的分析：")
        major = structured.get("major_analysis")
        if major and isinstance(major, dict):
            parts.append(str(major.get("description", ""))[:300])

    else:
        # Generic fallback
        parts.append("收到您的信息，以下是我的分析：")
        if reasoning:
            parts.append(reasoning[:500])

    # 先构建回复,再追加 disclaimer(来源标注由 source_attribution 节点处理)
    reply = "\n".join(parts)
    reply = reply + _DISCLAIMER

    trace = list(state.get("trace", []))
    trace.append({"node": "render_reply", "event": "reply_built"})

    # ``template`` tells the SSE handler this is a deterministic, grounded
    # draft. When an LLM key is configured the handler streams a model answer
    # instead, and uses this draft verbatim as the offline/failure fallback.
    return {"reply": reply, "trace": trace, "reply_source": "template", "template_reply": reply}
