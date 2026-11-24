"""Reasoning node — assembles the reasoning context for response generation."""

from __future__ import annotations

import json
from typing import Any


def reason_node(state: dict[str, Any]) -> dict[str, Any]:
    """Assemble reasoning context from all gathered information.

    This node builds a structured reasoning string that captures the
    key facts, data, and knowledge to inform the final response.
    Includes skill methodology context when available.
    """
    slots = state.get("slots", {})
    data = state.get("data_query_results", {})
    knowledge = state.get("knowledge_context", "")
    model = state.get("cognitive_model", "default")
    heuristics = state.get("decision_heuristics", [])
    emotion = state.get("emotion_state", "normal")
    quotes = state.get("expert_quotes", [])

    parts = []

    # User profile summary
    filled = {k: v for k, v in slots.items() if v}
    if filled:
        parts.append(f"用户画像: {json.dumps(filled, ensure_ascii=False)}")

    # Data results summary
    if data:
        data_keys = [k for k in data.keys() if k != "error"]
        if data_keys:
            parts.append(f"数据查询结果: {', '.join(data_keys)}")

        match_schools = data.get("match_schools", [])
        if match_schools:
            school_names = [s.get("school_name", s.get("name", "")) for s in match_schools[:5]]
            parts.append(f"匹配院校: {', '.join(s for s in school_names if s)}")

        rank_info = data.get("rank_info")
        if rank_info:
            parts.append(f"分数位次: {rank_info}")

    validation = state.get("validation") or {}
    if validation.get("single_source_count"):
        parts.append("录取数据来源提示: 当前记录只有单一来源，尚不能证明独立交叉核验。")

    # Knowledge context (includes skill methodology)
    if knowledge:
        truncated = knowledge[:800]
        parts.append(f"方法论与知识:\n{truncated}")

    # Expert quotes
    if quotes:
        quote_texts = [q.get("text", "") for q in quotes[:3]]
        parts.append(f"专家观点: {'; '.join(q for q in quote_texts if q)}")

    # Quality signals
    parts.append(f"情绪状态: {emotion}")
    parts.append(f"认知模型: {model}")
    if heuristics:
        parts.append(f"决策启发: {'; '.join(h[:50] for h in heuristics[:3])}")

    reasoning = "\n".join(parts) if parts else "暂无足够信息进行分析。"

    trace = list(state.get("trace", []))
    trace.append({"node": "reason", "event": "reasoning_assembled"})

    return {"reasoning": reasoning, "trace": trace}
