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
