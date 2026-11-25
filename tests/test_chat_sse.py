"""Tests for the chat SSE endpoint structured card output."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from server.auth import create_session_token
from server.main import app


@pytest.mark.asyncio
async def test_chat_sse_emits_structured_card():
    """SSE response should include a structured card event with all required fields."""
    token = create_session_token("test-sse-001")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={
                "session_id": "test-sse-001",
                "scene": "gaokao",
                "message": "河北考生600分物理类想学计算机普通家庭想就业",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Parse SSE events from the response body
    lines = response.text.split("\n")
    events = [line for line in lines if line.startswith("data: ")]
    assert len(events) > 0, "Expected at least one SSE data event"

    # Find the structured card event
    structured_found = False
    for event in events:
        payload = json.loads(event[len("data: ") :])
        if payload.get("type") == "structured":
            structured_found = True
            result = payload.get("result", {})
            assert "title" in result, "Structured card missing 'title'"
            assert "facts" in result, "Structured card missing 'facts'"
            assert "suggestions" in result, "Structured card missing 'suggestions'"
            assert "risks" in result, "Structured card missing 'risks'"
            assert "next_actions" in result, "Structured card missing 'next_actions'"
            break
    assert structured_found, "No structured card event found in SSE response"


@pytest.mark.asyncio
async def test_chat_sse_reports_no_evidence_distinctly(monkeypatch):
    """A healthy corpus with no match must stream rag_status=NO_EVIDENCE.

    NO_EVIDENCE is deliberately distinct from RAG_DISABLED/RAG_ERROR so the
    client can tell "nothing relevant" apart from "retrieval is broken". The
    graph is stubbed so the streaming contract is tested independently of
    intent routing.
    """

    class _NoEvidenceGraph:
        def invoke(self, state):
            return {
                "session_id": state["session_id"],
                "reply": "暂无匹配证据。",
                "reply_source": "template",
                "rag_state": "RAG_ENABLED",
                "rag_sources": [],
                "rag_reason": "corpus is available but holds no matching evidence",
            }

    monkeypatch.setattr("server.routes.chat.get_advisor_graph", lambda: _NoEvidenceGraph())
    token = create_session_token("test-sse-noev")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={"session_id": "test-sse-noev", "scene": "gaokao", "message": "平行志愿的量子缠结规则"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    events = [json.loads(line[len("data: ") :]) for line in response.text.split("\n") if line.startswith("data: ")]
    rag_statuses = [e for e in events if e.get("type") == "rag_status"]
    assert any(e.get("state") == "NO_EVIDENCE" for e in rag_statuses), rag_statuses


@pytest.mark.parametrize(
    "message",
    [
        "请比较北京大学和清华大学，结合专业约束制定志愿方案",
        "帮我分析多所院校并筛选符合选科的专业",
        "给我一个冲稳保选校规划",
        "Compare two schools and evaluate major constraints",
    ],
)
def test_managed_research_intent_detector_is_narrow(message):
    from server.routes.chat import requires_managed_admissions_research

    assert requires_managed_admissions_research(message)


@pytest.mark.parametrize(
    "message",
    ["河北考生600分物理类想学计算机", "请解释什么是平行志愿", "比较这两个分数的差距"],
)
def test_managed_research_intent_detector_keeps_simple_questions_on_chat(message):
    from server.routes.chat import requires_managed_admissions_research

    assert not requires_managed_admissions_research(message)


def test_managed_research_request_requires_complete_nonproduction_policy(monkeypatch):
    from server.routes.chat import _managed_research_request_enabled

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "standard")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", "true")
    assert not _managed_research_request_enabled()

    for name, value in {
        "ADMISSIONS_RESEARCH_DATASET_REF": "approved-catalog-v1",
        "ADMISSIONS_RESEARCH_JOURNAL": "/run/private/research.sqlite",
        "ADMISSIONS_RESEARCH_MODEL_ID": "openai:approved-model",
        "ADMISSIONS_RESEARCH_IMAGE_ID": "sha256:" + "a" * 64,
        "ADMISSIONS_RESEARCH_APPROVAL_REF": "approval-ref-2026-001",
    }.items():
        monkeypatch.setenv(name, value)
    assert _managed_research_request_enabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("requests_enabled", [True, False])
async def test_complex_chat_turn_is_routed_to_research_form_without_model_answer(monkeypatch, requests_enabled):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "standard")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_ENABLED", str(requests_enabled).lower())
    monkeypatch.setenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", str(requests_enabled).lower())
    for name, value in {
        "ADMISSIONS_RESEARCH_DATASET_REF": "approved-catalog-v1",
        "ADMISSIONS_RESEARCH_JOURNAL": "/run/private/research.sqlite",
        "ADMISSIONS_RESEARCH_MODEL_ID": "openai:approved-model",
        "ADMISSIONS_RESEARCH_IMAGE_ID": "sha256:" + "a" * 64,
        "ADMISSIONS_RESEARCH_APPROVAL_REF": "approval-ref-2026-001",
    }.items():
        monkeypatch.setenv(name, value)

    class _PreparationGraph:
        def invoke(self, state):
            return {
                **state,
                "reply": "unsafe free-form draft",
                "template_reply": "unsafe free-form draft",
                "reply_source": "template",
                "structured_result": {"title": "would-be recommendation"},
                "rag_state": "RAG_ENABLED",
                "rag_sources": [{"source": "synthetic"}],
                "trace": [],
            }

    monkeypatch.setattr("server.routes.chat.get_advisor_graph", lambda: _PreparationGraph())
    monkeypatch.setattr("server.routes.chat.load_llm_config", lambda: {"api_key": "configured-test-key"})
    monkeypatch.setattr(
        "server.routes.chat.generate_chat_candidate",
        lambda _state: pytest.fail("complex requests must not receive free-form model recommendations"),
    )
    monkeypatch.setattr(
        "server.routes.chat.quality_judge_node",
        lambda _state: pytest.fail("complex request routing must not call a paid Judge model"),
    )
    monkeypatch.setattr(
        "server.routes.chat.memory_node",
        lambda state: {**state, "trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            json={
                "session_id": "test-complex-research",
                "scene": "gaokao",
                "message": "请比较北京大学和清华大学，结合专业约束制定志愿方案",
            },
        )

    assert response.status_code == 200
    events = [json.loads(line[len("data: ") :]) for line in response.text.split("\n") if line.startswith("data: ")]
    assert any(event.get("type") == "research_intent" and event.get("enabled") is requests_enabled for event in events)
    assert not any(event.get("type") in {"structured", "sources"} for event in events)
    answer = "".join(event.get("content", "") for event in events if event.get("type") == "token")
    assert ("结构化研究表单" in answer) is requests_enabled
    assert ("当前环境未启用该功能" in answer) is not requests_enabled
    assert "unsafe free-form draft" not in answer
