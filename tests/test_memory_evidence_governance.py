"""Session-memory governance: evidence priority, fact quarantine, safe degradation."""

from __future__ import annotations

import pytest

from server.agent.chat_context import ChatContextError, build_chat_context, build_compacted_chat_context
from server.agent.memory_evidence import (
    extract_evidence_anchors,
    quarantine_summary,
    render_evidence_block,
)
from server.agent.memory_manager import SUMMARY_PROMPT


def _history(rounds: int = 8) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for index in range(rounds):
        history.append({"role": "user", "content": f"第{index}轮问题：我选的是物理类，倾向计算机专业。"})
        history.append(
            {
                "role": "assistant",
                "content": f"第{index}轮回复：已记录（来源：kb_2025 § 院校概览-{index}）。",
            }
        )
    return history


def test_evidence_anchors_are_extracted_from_assistant_turns_only():
    history = [
        {"role": "user", "content": "我听说（来源：user_made_up § 闲聊）"},
        {"role": "assistant", "content": "结论来自（来源：kb_2025 § 投档线）。"},
    ]
    anchors = extract_evidence_anchors(history)
    assert anchors == ["kb_2025 § 投档线"]
    assert "user_made_up" not in render_evidence_block(anchors)


def test_evidence_block_survives_history_truncation():
    context = build_chat_context(
        _history(8),
        "system",
        "当前任务：请给出建议。",
        keep_recent_rounds=2,
        max_estimated_tokens=8000,
    )
    rendered = "\n".join(item["content"] for item in context.messages)
    assert "kb_2025 § 院校概览-0" in rendered
    assert context.status == "PARTIAL_HISTORY"


def test_current_task_is_never_truncated_to_fit_the_budget():
    with pytest.raises(ChatContextError):
        build_chat_context(
            _history(8),
            "system",
            "当前任务：" + "很长的当前任务" * 4000,
            keep_recent_rounds=2,
            max_estimated_tokens=200,
        )


def test_summary_admissions_facts_are_quarantined():
    summary = (
        "1. 用户目标：找计算机专业。\n"
        "2. 明确约束：分数线620，位次12000。\n"
        "3. 未确认事项：需要重新查询。"
    )
    clean, redactions = quarantine_summary(summary)
    assert redactions == 1
    assert "620" not in clean
    assert "位次12000" not in clean
    assert "已屏蔽" in clean
    assert "用户目标：找计算机专业" in clean


def test_summary_citations_are_removed():
    clean, _ = quarantine_summary("结论（来源：kb_fake § 伪造引用）。")
    # The fabricated citation is dropped as a whole: a summary may not claim a
    # source marker it did not verify.
    assert "kb_fake" not in clean
    assert "来源" not in clean
    assert "结论" in clean


def test_compaction_renders_only_the_quarantined_summary():
    async def summarizer(_messages):
        return "1. 用户目标：找计算机专业。\n2. 明确约束：录取率95%。"

    import asyncio

    context = asyncio.run(
        build_compacted_chat_context(
            _history(8),
            "system",
            "当前任务：请给出建议。",
            summarizer=summarizer,
            keep_recent_rounds=2,
            summary_trigger_tokens=10,
            max_estimated_tokens=8000,
        )
    )
    rendered = "\n".join(item["content"] for item in context.messages)
    assert "95%" not in rendered
    assert "已屏蔽" in rendered


def test_compaction_failure_degrades_to_an_explicit_partial_history():
    async def failing(_messages):
        raise RuntimeError("provider unavailable")

    import asyncio

    context = asyncio.run(
        build_compacted_chat_context(
            _history(8),
            "system",
            "当前任务：请给出建议。",
            summarizer=failing,
            keep_recent_rounds=2,
            summary_trigger_tokens=10,
            max_estimated_tokens=8000,
        )
    )
    assert context.status == "COMPACTION_UNAVAILABLE"
    rendered = "\n".join(item["content"] for item in context.messages)
    assert "上下文不完整" in rendered
    assert "provider unavailable" not in rendered


def test_summary_prompt_forbids_admissions_numbers_and_fake_citations():
    assert "分数线" in SUMMARY_PROMPT
    assert "不得虚构" in SUMMARY_PROMPT
    assert "来源" in SUMMARY_PROMPT
