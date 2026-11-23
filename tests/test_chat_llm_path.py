"""Regression tests for the /chat Phase-2 answer path.

The preparation graph produces an offline-safe template. The finalization
phase must verify/persist the final answer before emitting any model text.
"""

from __future__ import annotations

import json

import pytest

import server.routes.chat as chat


def _events(raw: list[str]) -> list[dict]:
    return [json.loads(line[6:]) for line in raw if line.startswith("data: ")]


@pytest.fixture(autouse=True)
def _mock_finalization_io(monkeypatch):
    monkeypatch.setattr(
        chat,
        "memory_node",
        lambda state: {
            "trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]
        },
    )
    monkeypatch.setattr(chat, "quality_judge_node", lambda state: {"quality_grade": "pass"})


class _FakeGraph:
    def invoke(self, state: dict) -> dict:
        return {
            "reply": "模板答复",
            "reply_source": "template",
            "template_reply": "模板答复",
            "trace": [],
        }


@pytest.mark.asyncio
async def test_uses_llm_when_key_configured(monkeypatch) -> None:
    monkeypatch.setattr(chat, "get_advisor_graph", lambda: _FakeGraph())
    monkeypatch.setattr(chat, "load_llm_config", lambda: {"api_key": "sk-test"})
    async def _candidate(state):
        return "模型答复", False

    monkeypatch.setattr(chat, "generate_chat_candidate", _candidate)

    raw = [line async for line in chat._sse_generator("session-1234", "gaokao", "问题", None)]
    events = _events(raw)
    text = "".join(e.get("content", "") for e in events if e.get("type") == "token")

    assert "模型答复" in text
    assert "模板答复" not in text
    assert "声明：以上分析基于" in text


@pytest.mark.asyncio
async def test_uses_grounded_template_without_key(monkeypatch) -> None:
    monkeypatch.setattr(chat, "get_advisor_graph", lambda: _FakeGraph())
    monkeypatch.setattr(chat, "load_llm_config", lambda: {"api_key": ""})

    called = {"count": 0}

    async def _candidate(state):
        called["count"] += 1
        return "模型答复", False

    monkeypatch.setattr(chat, "generate_chat_candidate", _candidate)

    raw = [line async for line in chat._sse_generator("session-1234", "gaokao", "问题", None)]
    events = _events(raw)
    text = "".join(e.get("content", "") for e in events if e.get("type") == "token")

    assert "模板答复" in text
    assert "模型答复" not in text
    assert called["count"] == 0
    assert any(e.get("type") == "degraded" and "未配置 LLM Key" in e.get("message", "") for e in events)


def test_llm_stream_falls_back_to_grounded_template(monkeypatch) -> None:
    from server.graph.nodes import llm_node

    def _raise() -> None:
        raise RuntimeError("no key")

    monkeypatch.setattr(llm_node, "_get_llm_client", _raise)

    tokens = list(llm_node.llm_node_stream({"template_reply": "模板答复"}))

    assert "".join(text for text, _ in tokens) == "模板答复"
    assert all(degraded for _, degraded in tokens)
