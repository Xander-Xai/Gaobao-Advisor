"""AdvisorState — TypedDict defining the full graph state."""

from typing import Literal, TypedDict


class AdvisorState(TypedDict, total=False):
    """State passed through every node in the LangGraph advisor pipeline."""

    # ── Identity & Input ───────────────────────────────────────
    session_id: str
    user_id: str
    input_text: str
    scene: Literal["gaokao", "kaoyan", "career", "general"]

    # ── Conversation history ───────────────────────────────────
    messages: list[dict]

    # ── Slot extraction ────────────────────────────────────────
    slots: dict
    profile_snapshot: dict
    missing_fields: list[str]
    # Soul-query counter and already-asked fields. Persisted inside
    # Conversation.slots_json; it must be a declared channel or LangGraph
    # drops it between nodes.
    _query_state: dict

    # ── Quality pipeline ───────────────────────────────────────
    emotion_state: str
    cognitive_model: str
    decision_heuristics: list[str]
    anti_pattern_violations: list[dict]

    # ── Knowledge retrieval ────────────────────────────────────
    rag_chunks: list[dict]
    expert_quotes: list[dict]
    data_query_results: dict
    knowledge_context: str
    rag_state: str  # RAG_ENABLED | RAG_DISABLED | RAG_ERROR
    rag_evidence: bool  # True only when matching evidence was retrieved
    rag_sources: list[dict]  # citations for retrieved chunks
    rag_reason: str  # explanation when state != RAG_ENABLED or no evidence
    knowledge_query: bool  # input is a knowledge question (routing signal)
    validation: dict  # post-SQL per-school provenance checks; never cross-compare different schools
    major_risk: dict  # pre-generation major risk hint

    # ── Reasoning & Output ────────────────────────────────────
    reasoning: str
    structured_result: dict
    reply: str
    reply_source: str  # "template" (deterministic draft) | "graph" (final)
    template_reply: str  # grounded offline/failure fallback for the SSE layer
    confidence: float

    # ── Traceability ──────────────────────────────────────────
    trace: list[dict]

    # ── Quality Judge (LLM-as-Judge) ──────────────────────────
    quality_scores: dict  # 4 维度评分
    aggregate_score: float  # 聚合分
    hallucination_flags: list  # 幻觉标记列表
    quality_grade: str  # excellent / pass / fail

    # ── Feedback & Rewrite ────────────────────────────────────
    should_rewrite: bool  # post-check flagged rewrite
    needs_rewrite: bool  # feedback node confirms rewrite
    rewrite_attempts: int  # number of rewrite attempts made
