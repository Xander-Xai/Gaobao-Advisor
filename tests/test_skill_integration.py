"""Tests for SkillService integration into LangGraph nodes."""

from unittest.mock import MagicMock, patch

import pytest

from server.graph.graph import build_advisor_graph


@pytest.fixture(autouse=True)
def _mock_llm():
    """Mock the LLM client so integration tests don't call the real API."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="根据你的信息，我建议……"))]
    mock_client.chat.completions.create.return_value = mock_response

    with patch("server.graph.nodes.llm_node._get_llm_client", return_value=mock_client):
        yield


@pytest.fixture
def graph():
    return build_advisor_graph()


def test_quality_node_injects_skill_context(graph):
    """quality_orchestrate_node should produce skill_context in state."""
    result = graph.invoke(
        {
            "input_text": "我是河北考生，600分，想学计算机",
            "scene": "gaokao",
            "session_id": "test-skill-001",
            "slots": {"province": "河北", "score": "600分", "subject": "物理类"},
        }
    )
    reasoning = result.get("reasoning", "")
    assert isinstance(reasoning, str)
    # Skill context should be injected into reasoning
    assert "社会筛子论" in reasoning or "就业倒推法" in reasoning or len(reasoning) > 50


def test_reasoning_contains_mental_model(graph):
    """reason_node should include skill context when scene is gaokao."""
    result = graph.invoke(
        {
            "input_text": "我是河北考生，物理类，600分，想学计算机",
            "scene": "gaokao",
            "session_id": "test-skill-002",
            "slots": {"province": "河北", "score": "600分"},
        }
    )
    reasoning = result.get("reasoning", "")
    assert len(reasoning) > 0


def test_full_pipeline_with_skill(graph):
    """Full pipeline should still work with SkillService integrated."""
    result = graph.invoke(
        {
            "input_text": "河北考生600分物理类想学计算机普通家庭",
            "scene": "gaokao",
            "session_id": "test-skill-003",
            "slots": {},
        }
    )
    assert result.get("reply")
    assert result.get("structured_result")
    trace = result.get("trace", [])
    node_names = [t.get("node") for t in trace]
    assert "quality_orchestrate" in node_names
    assert "reason" in node_names


@pytest.mark.parametrize("rag_has_evidence", [True, False])
@pytest.mark.asyncio
async def test_online_skill_survives_retrieval_and_reaches_both_generation_paths(monkeypatch, rag_has_evidence):
    from server.graph.nodes import llm_node, quality_nodes, rag_node
    from skills.service import SkillService

    skill = SkillService()
    monkeypatch.setattr(quality_nodes, "_get_skill_service", lambda: skill)
    monkeypatch.setattr(llm_node, "_load_memory_for_session", lambda *_: [])
    monkeypatch.setenv("GAOBAO_CHAT_COMPACTION_ENABLED", "false")
    monkeypatch.setattr(
        rag_node.rag,
        "search",
        lambda *_: {
            "group_chunks": [{"text": "RAG_ONLY_EVIDENCE", "source": "test-source"}] if rag_has_evidence else [],
            "has_evidence": rag_has_evidence,
            "citations": [{"source": "test-source"}] if rag_has_evidence else [],
        },
    )
    state = {"scene": "gaokao", "input_text": "比较志愿", "slots": {}}
    state.update(quality_nodes.quality_orchestrate_node(state))
    state.update(rag_node.rag_retrieve_node(state))
    assert state["skill_sections"] == ["heuristics", "anti_patterns"]
    assert skill._safety_rules in state["skill_context"]
    assert "RAG_ONLY_EVIDENCE" not in state["skill_context"]
    assert ("RAG_ONLY_EVIDENCE" in state["knowledge_context"]) is rag_has_evidence
    for context in (
        llm_node._build_chat_context_sync(state, "BASE"),
        await llm_node._build_chat_context_async(state, "BASE"),
    ):
        assert skill._safety_rules in context.messages[0]["content"]
        assert skill._heuristics_text in context.messages[0]["content"]
        assert "RAG_ONLY_EVIDENCE" not in context.messages[0]["content"]


def test_online_skill_discloses_only_safety_for_factual_lookup(monkeypatch):
    from server.graph.nodes import quality_nodes
    from skills.service import SkillService

    skill = SkillService()
    monkeypatch.setattr(quality_nodes, "_get_skill_service", lambda: skill)
    state = quality_nodes.quality_orchestrate_node(
        {"scene": "gaokao", "knowledge_query": True, "input_text": "政策是什么", "slots": {}}
    )
    assert state["skill_sections"] == []
    assert state["skill_context"] == skill._safety_rules
