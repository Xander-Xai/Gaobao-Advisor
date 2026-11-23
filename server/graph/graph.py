"""LangGraph advisor graph — wires nodes into a compiled pipeline.

NOTE: The llm_reason node has been removed from the graph.
LLM streaming is handled directly by the SSE handler in chat.py
to avoid double LLM calls. The graph stops at structure_output
(complete path) or question_generate (incomplete path),
and the SSE handler streams tokens from llm_node_stream.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from server.graph.nodes.check import profile_check_node
from server.graph.nodes.data_nodes import data_query_node
from server.graph.nodes.data_validation import validate_data_node
from server.graph.nodes.extract import slot_extract_node
from server.graph.nodes.feedback_node import feedback_node
from server.graph.nodes.intent import intent_detect_node
from server.graph.nodes.judge_node import quality_judge_node
from server.graph.nodes.memory import memory_node as memory_update_node
from server.graph.nodes.post_check import quality_post_check_node
from server.graph.nodes.quality_nodes import quality_orchestrate_node
from server.graph.nodes.question import question_generate_node
from server.graph.nodes.rag_node import rag_retrieve_node
from server.graph.nodes.reason import reason_node
from server.graph.nodes.render import render_reply_node
from server.graph.nodes.route import scene_route_node
from server.graph.nodes.security_scan import security_scan_node
from server.graph.nodes.source_attribution import source_attribution_node
from server.graph.nodes.structure import structure_output_node
from server.graph.state import AdvisorState


def _profile_has_data(state: AdvisorState) -> str:
    """Routing function: complete profile goes to the full pipeline,
    incomplete profile goes to question generation.

    Knowledge questions (``knowledge_query``) run the full pipeline even with
    an incomplete profile: they are answerable from the knowledge corpus and
    should not be turned into profile-collection questions. The structured
    data node simply finds nothing to query when no profile slots are present,
    so the SQL and RAG paths coexist without either fabricating the other's
    answer.
    """
    if state.get("reply"):
        return "has_reply"
    missing = state.get("missing_fields", [])
    if not missing:
        return "complete"
    if state.get("knowledge_query"):
        return "complete"
    return "incomplete"


def build_advisor_graph(*, finalize: bool = True):
    """Build the full graph or a preparation-only graph for SSE finalization.

    The API must evaluate and persist the *actual* final model answer, not a
    template saved before the model runs. The legacy full graph remains usable
    by offline callers and existing graph-level regression tests.
    """
    graph = StateGraph(AdvisorState)

    # ── Register nodes (llm_reason removed) ─────────────────────
    graph.add_node("security_scan", security_scan_node)
    graph.add_node("intent_detect", intent_detect_node)
    graph.add_node("scene_route", scene_route_node)
    graph.add_node("slot_extract", slot_extract_node)
    graph.add_node("profile_check", profile_check_node)
    graph.add_node("question_generate", question_generate_node)
    graph.add_node("quality_orchestrate", quality_orchestrate_node)
    graph.add_node("data_query", data_query_node)
    graph.add_node("validate_data", validate_data_node)
    graph.add_node("rag_retrieve", rag_retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("structure_output", structure_output_node)
    graph.add_node("render_reply", render_reply_node)
    if finalize:
        graph.add_node("source_attribution", source_attribution_node)
        graph.add_node("quality_post_check", quality_post_check_node)
        graph.add_node("quality_judge", quality_judge_node)
        graph.add_node("feedback", feedback_node)
        graph.add_node("memory_update", memory_update_node)

    # ── Entry point ───────────────────────────────────────────
    graph.set_entry_point("security_scan")

    # ── Linear chain up to profile_check ──────────────────────
    graph.add_edge("security_scan", "intent_detect")
    graph.add_edge("intent_detect", "scene_route")
    graph.add_edge("scene_route", "slot_extract")
    graph.add_edge("slot_extract", "profile_check")

    # ── Conditional edge from profile_check ───────────────────
    graph.add_conditional_edges(
        "profile_check",
        _profile_has_data,
        {
            "complete": "quality_orchestrate",
            "incomplete": "question_generate",
            "has_reply": "render_reply",
        },
    )

    # ── Question path converges to render → memory → END ───────
    graph.add_edge("question_generate", "render_reply")

    # ── Full pipeline: structure_output → render (no llm_reason) ──
    graph.add_edge("quality_orchestrate", "data_query")
    graph.add_edge("data_query", "validate_data")
    graph.add_edge("validate_data", "rag_retrieve")
    graph.add_edge("rag_retrieve", "reason")
    graph.add_edge("reason", "structure_output")
    graph.add_edge("structure_output", "render_reply")

    # ── Converge: render → source_attribution → quality_post_check → quality_judge → memory → END ────
    if finalize:
        graph.add_edge("render_reply", "source_attribution")
        graph.add_edge("source_attribution", "quality_post_check")
        graph.add_edge("quality_post_check", "quality_judge")
        graph.add_edge("quality_judge", "feedback")
        graph.add_edge("feedback", "memory_update")
        graph.add_edge("memory_update", END)
    else:
        graph.add_edge("render_reply", END)

    return graph.compile()


# ── Singleton accessor ────────────────────────────────────────
_advisor_graph = None


def get_advisor_graph():
    """Return the compiled graph (lazy singleton)."""
    global _advisor_graph
    if _advisor_graph is None:
        _advisor_graph = build_advisor_graph()
    return _advisor_graph


_chat_preparation_graph = None


def get_chat_preparation_graph():
    """Return the graph without quality/persistence finalizers for SSE use."""
    global _chat_preparation_graph
    if _chat_preparation_graph is None:
        _chat_preparation_graph = build_advisor_graph(finalize=False)
    return _chat_preparation_graph
