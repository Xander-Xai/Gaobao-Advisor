"""Regression checks for final answer integrity and read-only SQL provenance."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.routes.chat as chat
from server.graph.graph import build_advisor_graph
from server.graph.nodes.data_validation import validate_data_node


def test_chat_preparation_does_not_persist_template():
    prepared = build_advisor_graph(finalize=False)
    complete = build_advisor_graph()
    assert "memory_update" not in prepared.nodes
    assert "quality_judge" not in prepared.nodes
    assert "memory_update" in complete.nodes


def test_single_source_does_not_cross_validate_different_schools():
    state = {
        "slots": {"province": "示例省"},
        "data_query_results": {
            "match_schools": [
                {"school_name": "A（合成）", "min_score": 550, "data_source": "synthetic-db"},
                {"school_name": "B（合成）", "min_score": 610, "data_source": "synthetic-db"},
            ]
        },
    }
    result = validate_data_node(state)["validation"]
    assert result["single_source_count"] == 2
    assert result["status"] == "SINGLE_SOURCE"
    assert all(x["source_count"] == 1 and x["confidence"] == "低" for x in result["school_checks"])


def _mock_finalizers(monkeypatch, observed):
    monkeypatch.setattr(chat, "quality_judge_node", lambda state: {"quality_grade": "pass"})

    def persist(state):
        observed.append((state["reply"], state["atomic_memory"]))
        return {"trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]}

    monkeypatch.setattr(chat, "memory_node", persist)


def test_unattributed_model_numeric_answer_falls_back(monkeypatch):
    stored = []
    _mock_finalizers(monkeypatch, stored)
    state = {"reply": "可信模板", "template_reply": "可信模板", "trace": [], "input_text": "问题"}
    final, degraded = chat._finalize_chat_answer(state, "该校最低录取分为666分。", model_generated=True)
    assert degraded
    assert "可信模板" in final["reply"]
    assert "666分" not in final["reply"]
    assert stored == [(final["reply"], True)]


@pytest.mark.asyncio
async def test_sse_emits_exact_persisted_final_answer(monkeypatch):
    captured = []
    _mock_finalizers(monkeypatch, captured)

    class FakeGraph:
        def invoke(self, state):
            return {"reply": "可信模板", "template_reply": "可信模板", "reply_source": "template", "trace": []}

    async def candidate(state):
        return "模型解释内容", False

    monkeypatch.setattr(chat, "get_advisor_graph", lambda: FakeGraph())
    monkeypatch.setattr(chat, "_load_chat_state", lambda session_id, slots: ({}, {}))
    monkeypatch.setattr(chat, "load_llm_config", lambda: {"api_key": "sk-test"})
    monkeypatch.setattr(chat, "generate_chat_candidate", candidate)

    raw = [item async for item in chat._sse_generator("test-final", "gaokao", "问题", None)]
    events = [json.loads(item[6:]) for item in raw if item.startswith("data: ")]
    delivered = "".join(e["content"] for e in events if e["type"] == "token")
    assert events[-1]["type"] == "done"
    assert delivered.startswith("模型解释内容")
    assert captured == [(delivered, True)]


@pytest.mark.asyncio
async def test_async_generation_discards_partial_content_on_network_error(monkeypatch):
    from server.graph.nodes import llm_node

    class PartialStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not getattr(self, "sent", False):
                self.sent = True
                return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="部分文本"))])
            raise ConnectionError("stream interrupted")

    class FakeCompletion:
        async def create(self, **kwargs):
            return PartialStream()

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletion())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(llm_node, "AsyncOpenAI", lambda **kwargs: FakeClient())
    monkeypatch.setattr(llm_node, "_get_config", lambda: {"api_key": "sk-test", "model": "fake"})
    monkeypatch.setattr(llm_node, "_load_memory_for_session", lambda session_id: [])
    answer, degraded = await llm_node.generate_chat_candidate(
        {"session_id": "test", "reasoning": "模拟内容", "template_reply": "可信模板"}
    )
    assert (answer, degraded) == ("可信模板", True)
    assert "部分文本" not in answer


def test_memory_atomic_final_turn_commits_once(monkeypatch):
    from server.graph.nodes import memory as memory_module

    db = MagicMock()
    conv = MagicMock()
    conv.id = 1
    conv.messages = []
    msg = MagicMock()
    slots = MagicMock()

    monkeypatch.setattr(memory_module, "get_session", lambda: db)
    monkeypatch.setattr(memory_module, "get_or_create_conversation", lambda db, sid: conv)
    monkeypatch.setattr(memory_module, "load_conversation_slots", lambda db, sid: {})
    monkeypatch.setattr(memory_module, "save_message", msg)
    monkeypatch.setattr(memory_module, "save_slots", slots)

    result = memory_module.memory_node(
        {
            "session_id": "test-atomic",
            "input_text": "本轮用户提问",
            "reply": "最终已校验回答",
            "slots": {},
            "atomic_memory": True,
        }
    )
    assert result["trace"][-1]["event"] == "persisted"
    assert msg.call_count == 2
    assert all(call.kwargs.get("commit") is False for call in msg.call_args_list)
    assert slots.call_args.kwargs.get("commit") is False
    db.commit.assert_called_once()


def test_judge_rejection_falls_back_and_discards_untrusted_score(monkeypatch):
    captured = []
    monkeypatch.setattr(
        chat,
        "quality_judge_node",
        lambda state: {
            "quality_grade": "fail",
            "quality_scored": True,
            "quality_scores": {"factual": 5},
            "aggregate_score": 5.0,
            "hallucination_flags": ["contradiction"],
        },
    )
    monkeypatch.setattr(
        chat,
        "memory_node",
        lambda state: (
            captured.append(dict(state))
            or {"trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]}
        ),
    )
    state = {"reply": "保守模板", "template_reply": "保守模板", "input_text": "问题", "trace": []}
    final, degraded = chat._finalize_chat_answer(state, "看似正常的模型回答。", model_generated=True)
    assert degraded
    assert "保守模板" in final["reply"]
    assert "看似正常的模型回答" not in final["reply"]
    assert final["quality_scored"] is False
    assert final["aggregate_score"] is None
    assert final["review_reason_codes"] == ["JUDGE_REJECTED"]
    assert captured[0]["reply"] == final["reply"]


def test_unscored_judge_never_certifies_model_candidate(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        chat,
        "quality_judge_node",
        lambda state: {"quality_grade": "unscored", "quality_scored": False},
    )
    monkeypatch.setattr(
        chat,
        "memory_node",
        lambda state: (
            recorded.append(state["reply"])
            or {"trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]}
        ),
    )
    state = {"reply": "可信模板", "template_reply": "可信模板", "input_text": "问题", "trace": []}
    final, degraded = chat._finalize_chat_answer(state, "模型未知答案", model_generated=True)
    assert degraded
    assert final["review_reason_codes"] == ["JUDGE_UNAVAILABLE"]
    assert "模型未知答案" not in recorded[0]


@pytest.mark.asyncio
async def test_single_rewrite_passes_new_candidate_through_final_gate(monkeypatch):
    persisted = []
    calls = []
    _mock_finalizers(monkeypatch, persisted)

    class FakeGraph:
        def invoke(self, state):
            return {
                "reply": "本地模板",
                "template_reply": "本地模板",
                "reply_source": "template",
                "trace": [],
            }

    async def initial_candidate(state):
        return "我保证今年这所学校录取线是666分。", False

    async def corrected(state, draft, reasons):
        calls.append((draft, reasons))
        return "缺少可靠招生数据，当前无法核验，请查询官方招生公告。"

    monkeypatch.setattr(chat, "get_advisor_graph", lambda: FakeGraph())
    monkeypatch.setattr(chat, "_load_chat_state", lambda session_id, slots: ({}, {}))
    monkeypatch.setattr(chat, "load_llm_config", lambda: {"api_key": "test-configured"})
    monkeypatch.setattr(chat, "generate_chat_candidate", initial_candidate)
    monkeypatch.setattr(chat, "generate_chat_rewrite", corrected)
    monkeypatch.setattr(chat, "chat_rewrite_enabled", lambda: True)

    data = [item async for item in chat._sse_generator("test-rewrite", "gaokao", "问题", None)]
    events = [json.loads(line[6:]) for line in data if line.startswith("data: ")]
    delivered = "".join(e["content"] for e in events if e["type"] == "token")
    assert len(calls) == 1
    assert "UNATTRIBUTED_NUMBER" in calls[0][1]
    assert "UNVERIFIED_NUMERIC_CLAIM" in calls[0][1]
    assert "官方招生公告" in delivered
    assert "666" not in delivered
    assert persisted == [(delivered, True)]
    assert not any(e["type"] == "review" for e in events)


@pytest.mark.asyncio
async def test_rewrite_that_still_invents_numbers_requires_review(monkeypatch):
    persisted = []
    _mock_finalizers(monkeypatch, persisted)

    class FakeGraph:
        def invoke(self, state):
            return {
                "reply": "保守模板",
                "template_reply": "保守模板",
                "reply_source": "template",
                "trace": [],
            }

    async def candidate(state):
        return "学校录取线为666分。", False

    async def bad_rewrite(state, draft, reasons):
        return "根据我的消息，学校录取线为999分。"

    monkeypatch.setattr(chat, "get_advisor_graph", lambda: FakeGraph())
    monkeypatch.setattr(chat, "_load_chat_state", lambda sid, slots: ({}, {}))
    monkeypatch.setattr(chat, "load_llm_config", lambda: {"api_key": "test-configured"})
    monkeypatch.setattr(chat, "generate_chat_candidate", candidate)
    monkeypatch.setattr(chat, "generate_chat_rewrite", bad_rewrite)
    monkeypatch.setattr(chat, "chat_rewrite_enabled", lambda: True)
    raw = [item async for item in chat._sse_generator("test-unsafe-rewrite", "gaokao", "问题", None)]
    events = [json.loads(line[6:]) for line in raw if line.startswith("data: ")]
    delivered = "".join(e["content"] for e in events if e["type"] == "token")
    assert "保守模板" in delivered
    assert "999" not in delivered
    assert any(e["type"] == "review" and e["status"] == "required" for e in events)
    assert persisted == [(delivered, True)]


@pytest.mark.asyncio
async def test_rewrite_opt_in_and_one_provider_call(monkeypatch):
    from server.quality import chat_repair

    fake_calls = []

    class ChatCompletions:
        async def create(self, **kwargs):
            fake_calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="已清除无法核验的数字。"))])

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            self.chat = SimpleNamespace(completions=ChatCompletions())
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("CHAT_REWRITE_ENABLED", "false")
    monkeypatch.setattr(chat_repair, "load_llm_config", lambda: {"api_key": "test", "model": "fake"})
    monkeypatch.setattr(chat_repair, "AsyncOpenAI", Client)
    assert await chat_repair.generate_chat_rewrite({"input_text": "问题"}, "666分", ["UNATTRIBUTED_NUMBER"]) is None
    assert fake_calls == []

    monkeypatch.setenv("CHAT_REWRITE_ENABLED", "true")
    assert (
        await chat_repair.generate_chat_rewrite({"input_text": "问题"}, "666分", ["UNATTRIBUTED_NUMBER"])
        == "已清除无法核验的数字。"
    )
    assert len(fake_calls) == 1
    assert fake_calls[0]["stream"] is False



def test_unscored_judge_does_not_emit_fake_metrics_or_quality_record(monkeypatch):
    from quality.judge.scorecard import DimensionScore, JudgeResult
    from server.graph.nodes import judge_node
    from server import metrics
    from db import database

    calls = []
    unscored = JudgeResult(
        scores={"factual": 50.0, "relevance": 50.0, "helpfulness": 50.0, "style": 50.0},
        aggregate_score=50.0,
        dimensions=[DimensionScore(dimension="factual", score=50.0, unscored=True)],
        hallucination_flags=[],
        judge_model="missing",
        latency_ms=0,
    )

    class StubJudge:
        def evaluate_sync(self, query, reply, context):
            return unscored

    monkeypatch.setattr(judge_node, "QualityJudge", lambda: StubJudge())
    monkeypatch.setattr(metrics, "record_judge_score", lambda *args: calls.append(args))
    monkeypatch.setattr(database, "SessionLocal", lambda: (_ for _ in ()).throw(AssertionError("unexpected DB")))
    result = judge_node.quality_judge_node({"reply": "暂无可验证依据", "input_text": "问题", "session_id": "fake"})
    assert result["quality_grade"] == "unscored"
    assert result["quality_scored"] is False
    assert calls == []
