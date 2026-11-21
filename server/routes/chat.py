"""Chat endpoint with SSE streaming — backed by LangGraph workflow."""

import asyncio
import json
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config.loader import load_llm_config
from db.database import get_session
from db.models import Highlight
from server.auth import create_session_token, require_session_auth, verify_session_token
from server.graph.graph import get_advisor_graph
from server.graph.nodes.llm_node import llm_node_stream
from server.graph.nodes.render import _DISCLAIMER
from slots.persistence import extract_query_state, flatten_slots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str = Field(
        ...,
        min_length=4,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="会话ID：4-64位字母数字下划线连字符",
    )
    scene: str = "gaokao"
    message: str = Field(..., min_length=1, max_length=3000)
    slots: dict | None = None


def _load_chat_state(session_id: str, provided_slots: dict | None) -> tuple[dict, dict]:
    """Load persisted slots/query state and flatten them for the graph.

    The database is the source of truth for an authenticated session. Client
    slots only fill keys the database does not know yet, which keeps a stale
    browser tab from overwriting a profile edited through ``/api/v1/profile``.
    """
    from db.crud import load_conversation_slots
    from db.database import get_session

    stored: dict = {}
    db = None
    try:
        db = get_session()
        stored = load_conversation_slots(db, session_id) or {}
    except Exception:
        logger.warning("Could not load persisted slots for session %s", session_id, exc_info=True)
    finally:
        if db is not None:
            db.close()

    slots = flatten_slots(stored)
    for key, value in (provided_slots or {}).items():
        if key not in slots and value not in (None, "", False):
            slots[key] = value
    return slots, extract_query_state(stored)


async def _sse_generator(
    session_id: str,
    scene: str,
    message: str,
    existing_slots: dict | None,
    user_memory: list[dict] | None = None,
):
    """Yield SSE events for a chat response via the LangGraph pipeline.

    Two-phase streaming:
    1. Run graph up to LLM node (yields metadata: slots, emotion, structured)
    2. Stream LLM tokens in real-time via llm_node_stream

    The final 'done' event returns a session_token for Bearer auth
    on subsequent requests (profile/voice endpoints).
    """
    graph = get_advisor_graph()
    persisted_slots, query_state = await asyncio.to_thread(_load_chat_state, session_id, existing_slots)
    initial_state = {
        "session_id": session_id,
        "input_text": message,
        "scene": scene,
        "slots": persisted_slots,
        "user_memory": user_memory or [],
        "messages": [],
        "trace": [],
    }
    if query_state:
        initial_state["_query_state"] = query_state

    # Phase 1: Run graph (synchronous, offloaded to thread)
    try:
        result = await asyncio.to_thread(graph.invoke, initial_state)
    except Exception:
        logger.exception("Phase 1 graph.invoke failed for session %s", session_id)
        yield f"data: {json.dumps({'type': 'error', 'code': 'GRAPH_FAILED', 'message': '服务暂时不可用，请稍后重试'})}\n\n"
        return

    # Emit updated slots
    if result.get("slots"):
        yield f"data: {json.dumps({'type': 'slots', 'data': result['slots']})}\n\n"

    # Emit emotion state
    if result.get("emotion_state"):
        yield f"data: {json.dumps({'type': 'emotion', 'state': result['emotion_state']})}\n\n"

    # Emit structured result if present
    if result.get("structured_result"):
        yield f"data: {json.dumps({'type': 'structured', 'result': result['structured_result']})}\n\n"

    # Emit retrieval evidence/citations so the client can show sources, and
    # report degraded retrieval explicitly instead of hiding it. A healthy
    # corpus with no matching chunk is its own state (NO_EVIDENCE) — distinct
    # from RAG_DISABLED (switched off) and RAG_ERROR (broken/missing corpus).
    if result.get("rag_state") == "RAG_ENABLED" and result.get("rag_sources"):
        yield f"data: {json.dumps({'type': 'sources', 'data': result['rag_sources'], 'has_evidence': True})}\n\n"
    elif result.get("rag_state") == "RAG_ENABLED":
        yield f"data: {json.dumps({'type': 'rag_status', 'state': 'NO_EVIDENCE', 'reason': result.get('rag_reason', '')})}\n\n"
    elif result.get("rag_state") in ("RAG_DISABLED", "RAG_ERROR"):
        yield f"data: {json.dumps({'type': 'rag_status', 'state': result['rag_state'], 'reason': result.get('rag_reason', '')})}\n\n"

    # Phase 2: build the answer.
    # Question/security replies are final and must not be rewritten. The
    # grounded template is used offline; when a key is configured the model
    # streams instead (the template remains the failure fallback).
    reply = result.get("reply")
    is_template = result.get("reply_source") == "template"
    llm_configured = bool(str(load_llm_config().get("api_key", "")).strip())

    if reply and not (is_template and llm_configured):
        if is_template:
            yield f"data: {json.dumps({'type': 'degraded', 'message': '未配置 LLM Key，已使用基于结构化数据的确定性模板答复'})}\n\n"
        chunk_size = 20
        for i in range(0, len(reply), chunk_size):
            chunk = reply[i : i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
    else:
        full_reply = []
        degraded = False
        for token, is_degraded in llm_node_stream(result):
            full_reply.append(token)
            degraded = is_degraded
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
        result["reply"] = "".join(full_reply)
        if degraded:
            yield f"data: {json.dumps({'type': 'degraded', 'message': 'AI 服务暂时不稳定，已启用降级回复'})}\n\n"

    # Every answer carries the synthetic-data boundary, including LLM output.
    if result.get("reply") and "声明：以上分析基于" not in result["reply"]:
        result["reply"] += _DISCLAIMER
        yield f"data: {json.dumps({'type': 'token', 'content': _DISCLAIMER})}\n\n"

    yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'message_id': f'{session_id}-response', 'session_token': create_session_token(session_id)})}\n\n"


@router.post("/chat")
async def chat(
    request: ChatRequest,
    authorization: str | None = Header(None),
    x_user_memory_token: str | None = Header(None),
):
    """Anonymous clients get a *new server-owned* session; existing sessions need proof.

    Never mint a bearer credential for an unauthenticated, caller-selected ID:
    that would turn knowledge of a session ID into access to its profile.
    """
    if authorization is None:
        # Ignore the untrusted session_id (which may name another user's session).
        # The generated ID has 192 bits of randomness and fits ChatRequest's shape.
        session_id = "session-" + secrets.token_urlsafe(24)
    else:
        if not authorization.startswith("Bearer ") or not verify_session_token(
            request.session_id, authorization.removeprefix("Bearer ").strip()
        ):
            raise HTTPException(status_code=401, detail="Invalid session token")
        session_id = request.session_id

    # Validate the independent vault credential BEFORE streaming starts. A bad
    # credential must produce HTTP 401/503, not a partial SSE response.
    memory_items: list[dict] = []
    if x_user_memory_token is not None:
        from server.user_memory import MemoryDisabled, MemoryUnauthorized, list_memories

        try:
            memory_items = list_memories(x_user_memory_token)
        except MemoryDisabled as exc:
            raise HTTPException(status_code=503, detail="User memory is not enabled") from exc
        except MemoryUnauthorized as exc:
            raise HTTPException(status_code=401, detail="Invalid memory credential") from exc
        except Exception as exc:
            logger.error("Memory read failed (error type: %s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="User memory storage unavailable") from exc

    return StreamingResponse(
        _sse_generator(
            session_id,
            request.scene,
            request.message,
            request.slots,
            memory_items,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class FeedbackRequest(BaseModel):
    """用户反馈请求"""

    session_id: str = Field(..., min_length=4, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    message_index: int = Field(..., ge=0, le=10000)
    rating: str = Field(..., pattern=r"^(helpful|not_helpful)$")
    feedback_text: str | None = Field(default=None, max_length=2000)
    quality_score_id: int | None = None


@router.post("/chat/feedback")
async def submit_feedback(request: FeedbackRequest, authorization: str | None = Header(None)):
    """提交用户反馈（有帮助/没帮助）— 仅限会话所有者。"""
    require_session_auth(request.session_id, authorization)

    from server.quality.feedback import FeedbackCollector

    collector = FeedbackCollector()
    ok = collector.save_feedback(
        conversation_id=request.session_id,
        message_index=request.message_index,
        rating=request.rating,
        feedback_text=request.feedback_text,
        quality_score_id=request.quality_score_id,
    )
    if ok:
        return {"success": True}
    # Generic failure detail: never surface storage/exception internals.
    raise HTTPException(status_code=500, detail="Failed to save feedback")


class HighlightExtractRequest(BaseModel):
    """金句提取请求"""

    session_id: str = Field(..., min_length=4, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    content: str = Field(..., min_length=10, max_length=2000)
    score: int = Field(default=0, ge=0, le=100)


@router.post("/chat/highlight")
async def submit_highlight(request: HighlightExtractRequest, authorization: str | None = Header(None)):
    """提取金句 — 仅限会话所有者。"""
    require_session_auth(request.session_id, authorization)

    db = None
    try:
        hl = Highlight(
            session_id=request.session_id,
            content=request.content,
            score=request.score,
        )
        db = get_session()
        db.add(hl)
        db.commit()
        return {"success": True}
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 - rollback best-effort, still fail closed
                logger.exception("Highlight rollback failed")
        logger.exception("Highlight save failed")
        raise HTTPException(status_code=500, detail="Failed to save highlight") from None
    finally:
        if db is not None:
            db.close()
