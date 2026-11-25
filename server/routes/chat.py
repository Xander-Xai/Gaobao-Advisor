"""Chat endpoint with SSE streaming — backed by LangGraph workflow."""

import asyncio
import json
import logging
import os
import re
import secrets

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config.loader import load_llm_config
from db.database import get_session
from db.models import Highlight
from server.auth import create_session_token, require_session_auth, verify_session_token
from server.graph.graph import get_chat_preparation_graph as get_advisor_graph
from server.graph.nodes.feedback_node import feedback_node
from server.graph.nodes.judge_node import quality_judge_node
from server.graph.nodes.llm_node import generate_chat_candidate
from server.graph.nodes.memory import memory_node
from server.graph.nodes.post_check import quality_post_check_node
from server.graph.nodes.render import _DISCLAIMER
from server.graph.nodes.source_attribution import source_attribution_node
from server.quality.chat_repair import chat_rewrite_enabled, generate_chat_rewrite
from server.quality.school_score_gate import has_unbound_school_score
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
    from server.industrial_runtime import industrial_enabled

    industrial = industrial_enabled()
    stored: dict = {}
    db = None
    try:
        db = get_session()
        if industrial:
            stored = load_conversation_slots(db, session_id, strict=True) or {}
        else:
            stored = load_conversation_slots(db, session_id) or {}
    except Exception:
        if industrial:
            raise RuntimeError("Session storage unavailable") from None
        logger.warning("Could not load persisted slots for session %s", session_id, exc_info=True)
    finally:
        if db is not None:
            db.close()

    slots = flatten_slots(stored, derive_score=not industrial)
    for key, value in (provided_slots or {}).items():
        if key not in slots and value not in (None, "", False):
            slots[key] = value
    return slots, extract_query_state(stored)


_ASSERTED_NUMBER = re.compile(r"(?<![0-9])(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d{4,7})\s*(?:分|位次|%|元/月|元/年|万元|万人)")
_EVIDENCE_NUMBER = re.compile(r"(?<![0-9])\d+(?:\.\d+)?(?![0-9])")
_MANAGED_RESEARCH_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:比较|对比).{0,36}(?:院校|学校|大学|专业|志愿|选校)",
        r"(?:院校|学校|大学|专业).{0,24}(?:比较|对比|筛选|排序|规划|方案)",
        r"(?:多校|多院校|多专业).{0,30}(?:研究|分析|比较|筛选|规划)",
        r"(?:志愿|选校|选专业).{0,16}(?:方案|规划|组合|约束|冲稳保)",
        r"\bcompare\b.{0,36}\b(?:schools?|universities|colleges|majors)\b",
    )
)


def requires_managed_admissions_research(message: str) -> bool:
    """Conservatively identify explicit multi-school/constraint requests.

    This is a routing hint only: it does not infer a task scope or authorize a
    provider call. False positives are preferable to an unverified model answer.
    """
    return any(pattern.search(message) for pattern in _MANAGED_RESEARCH_PATTERNS)


def _managed_research_request_enabled() -> bool:
    return (
        os.getenv("APP_ENV", "development").strip().lower() != "production"
        and os.getenv("GAOBAO_RUNTIME_PROFILE", "").strip().lower() != "industrial"
        and os.getenv("ADMISSIONS_RESEARCH_ENABLED", "").strip().lower() == "true"
        and os.getenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", "").strip().lower() == "true"
        and all(
            os.getenv(name, "").strip()
            for name in (
                "ADMISSIONS_RESEARCH_DATASET_REF",
                "ADMISSIONS_RESEARCH_JOURNAL",
                "ADMISSIONS_RESEARCH_MODEL_ID",
                "ADMISSIONS_RESEARCH_IMAGE_ID",
                "ADMISSIONS_RESEARCH_APPROVAL_REF",
            )
        )
    )


def _unsupported_numbers(state: dict, candidate: str) -> bool:
    """Cheap evidence containment gate, NOT semantic fact verification.

    A forged '根据/来源' marker cannot certify an unseen numeric value. This
    intentionally errs toward rejecting unverifiable statistics. Having a
    number somewhere in evidence still does NOT prove a claim about a
    particular school; human/semantic review remains necessary.
    """
    raw = [str(state.get("input_text") or "")]
    for key in ("slots", "data_query_results", "rag_chunks"):
        raw.append(json.dumps(state.get(key) or {}, ensure_ascii=False, default=str))
    observed = {m.group(0) for m in _EVIDENCE_NUMBER.finditer("\n".join(raw).replace(",", ""))}
    return any(m.group(1).replace(",", "") not in observed for m in _ASSERTED_NUMBER.finditer(candidate))


def _deterministic_gate_issues(state: dict, candidate: str) -> list[str]:
    """Return stable, redacted reason codes; never treat a marker as proof."""
    draft = dict(state)
    draft["reply"] = candidate
    checked = source_attribution_node(draft)
    draft.update(checked)
    draft.update(quality_post_check_node(draft))
    issues = []
    if "（数据来源待补全）" in draft.get("reply", ""):
        issues.append("UNATTRIBUTED_NUMBER")
    if draft.get("should_rewrite"):
        issues.append("ANTI_PATTERN")
    if _unsupported_numbers(state, candidate):
        issues.append("UNVERIFIED_NUMERIC_CLAIM")
    elif has_unbound_school_score(state, candidate):
        issues.append("UNBOUND_SCHOOL_SCORE")
    return issues


def _finalize_chat_answer(
    state: dict,
    candidate: str,
    *,
    model_generated: bool,
    rewrite_attempts: int = 0,
    turn_version=None,
    turn_key=None,
) -> tuple[dict, bool]:
    """Verify the exact answer, then persist it once before SSE publishes it.

    A deterministic answer/fallback is deliberately preferred to a risky model
    completion. The LLM-as-Judge is diagnostic: a 50-point *unscored* fallback
    must not be mistaken for evidence of safety or a verified business result.
    """
    from server.industrial_runtime import industrial_enabled

    final = dict(state)
    if industrial_enabled():
        from server.services.industrial_admissions import build_history_reply

        final["reply"], final["structured_result"] = build_history_reply(final)
        final["atomic_memory"] = True
        final["industrial_turn_version"] = turn_version
        final["industrial_turn_key"] = turn_key
        final.update(memory_node(final))
        final.pop("industrial_turn_version", None)
        final.pop("industrial_turn_key", None)
        if not any(
            event.get("node") == "memory_update" and event.get("event") == "persisted"
            for event in final.get("trace", [])
        ):
            raise RuntimeError("Final-answer persistence failed")
        return final, False
    fallback = final.get("template_reply") or final.get("reply") or "当前信息不足，无法完成分析。"
    if not candidate.strip():
        candidate, model_generated = fallback, False

    if model_generated and final.get("knowledge_query") and not final.get("rag_evidence"):
        # A SQL score row does not establish the text of an admissions policy.
        candidate = "当前知识库未收录足够的政策或规则依据，无法验证该问题。请查阅当地教育考试院官方公告。"
        model_generated = False

    def check(text: str) -> dict:
        draft = dict(final)
        if final.get("research_request_intent"):
            notice = "声明：这只是受控研究入口提示，不是录取建议；提交后仍需审批和独立证据核验。"
            draft["reply"] = text if "声明：" in text else text + "\n\n" + notice
        else:
            draft["reply"] = text if "声明：以上分析基于" in text else text + _DISCLAIMER
        draft.update(source_attribution_node(draft))
        draft.update(quality_post_check_node(draft))
        return draft

    checked = check(candidate)
    degraded = False
    review_reasons = []
    if model_generated:
        review_reasons = _deterministic_gate_issues(final, candidate)
        if review_reasons:
            # Reject the untrusted draft, not just its offending sentence.
            checked = check(fallback)
            degraded = True

    try:
        if final.get("research_request_intent"):
            # This turn only routes the user to the approval form. Avoid even a
            # separate paid Judge request; the response is a deterministic notice.
            checked["quality_grade"] = "unscored"
            checked["quality_scored"] = False
            checked["quality_scores"] = {}
            checked["hallucination_flags"] = []
            checked["aggregate_score"] = None
        else:
            # Scoring is computed on the proposed final answer, without writing a
            # quality row before its owning conversation message exists.
            checked.update(quality_judge_node({**checked, "defer_quality_persistence": True}))
            if (
                model_generated
                and not degraded
                and (
                    checked.get("quality_grade") in {"fail", "unscored"}
                    or checked.get("hallucination_flags")
                    or checked.get("judge_needs_rewrite")
                )
            ):
                review_reasons.append(
                    "JUDGE_UNAVAILABLE" if checked.get("quality_grade") == "unscored" else "JUDGE_REJECTED"
                )
                checked = check(fallback)
                degraded = True
                # The rejected model score must not be reported as the delivered
                # fallback's quality score; it has not been independently graded.
                checked["quality_grade"] = "unscored"
                checked["quality_scored"] = False
                checked["quality_scores"] = {}
                checked["hallucination_flags"] = []
                checked["aggregate_score"] = None
    except Exception:
        logger.exception("Final-answer quality evaluation failed")
        if model_generated and not degraded:
            review_reasons.append("JUDGE_ERROR")
            checked = check(fallback)
            degraded = True
        checked["quality_grade"] = "unscored"
        checked["quality_scored"] = False

    # No model rewriting is asserted unless an explicit attempt happened.
    checked["rewrite_attempts"] = min(max(rewrite_attempts, 0), 1)
    checked["review_required"] = bool(review_reasons)
    checked["review_reason_codes"] = sorted(set(review_reasons))
    checked.update(feedback_node(checked))
    checked["atomic_memory"] = True
    checked.update(memory_node(checked))
    if not any(
        event.get("node") == "memory_update" and event.get("event") == "persisted" for event in checked.get("trace", [])
    ):
        raise RuntimeError("Final-answer persistence failed")
    return checked, degraded


def _receipt_events(session_id: str, receipt: dict):
    """Both original and retry use the same committed reply and message identity."""
    if receipt["slots"]:
        yield {"type": "slots", "data": receipt["slots"]}
    yield {"type": "structured", "result": receipt["structured_result"]}
    reply = receipt["reply"]
    for offset in range(0, len(reply), 20):
        yield {"type": "token", "content": reply[offset : offset + 20]}
    yield {
        "type": "done",
        "session_id": session_id,
        "message_id": receipt["message_id"],
        "idempotency_supported": True,
        "session_token": create_session_token(session_id),
    }


async def _sse_generator(
    session_id: str,
    scene: str,
    message: str,
    existing_slots: dict | None,
    user_memory: list[dict] | None = None,
    *,
    fresh: bool = False,
    turn_key=None,
):
    """Prepare graph state, generate a candidate and validate/persist before SSE.

    The graph used here ends at render_reply. This endpoint alone owns the
    final source gate, quality evaluation and durable conversation turn. No
    unchecked provider chunks are ever emitted to the browser.
    """
    from server.industrial_runtime import industrial_enabled

    if turn_key is not None:
        from server.services.industrial_chat_receipt import RequestKeyConflict, lookup_receipt

        try:
            receipt = await asyncio.to_thread(lookup_receipt, session_id, turn_key)
        except RequestKeyConflict:
            yield f"data: {json.dumps({'type': 'error', 'code': 'IDEMPOTENCY_CONFLICT', 'message': '请求键已用于其他请求'})}\n\n"
            return
        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'code': 'SESSION_UNAVAILABLE', 'message': '会话服务暂时不可用'})}\n\n"
            return
        if receipt is not None:
            for event in _receipt_events(session_id, receipt):
                yield f"data: {json.dumps(event)}\n\n"
            return

    turn_version = None
    try:
        if industrial_enabled():
            from server.services.industrial_session import load_turn

            persisted_slots, query_state, turn_version = await asyncio.to_thread(
                load_turn, session_id, existing_slots, fresh=fresh
            )
        else:
            persisted_slots, query_state = await asyncio.to_thread(_load_chat_state, session_id, existing_slots)
    except Exception:
        yield f"data: {json.dumps({'type': 'error', 'code': 'SESSION_UNAVAILABLE', 'message': '会话服务暂时不可用'})}\n\n"
        return
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

    research_intent = not industrial_enabled() and requires_managed_admissions_research(message)
    try:
        if research_intent:
            # Reuse the existing security gate, then go directly to the
            # approval-form prompt. Do not run SQL/RAG preparation, a provider
            # model, or the paid quality judge on this routing turn.
            from server.graph.nodes.security_scan import security_scan_node

            result = dict(initial_state)
            result.update(await asyncio.to_thread(security_scan_node, initial_state))
        else:
            graph = get_advisor_graph()
            result = await asyncio.to_thread(graph.invoke, initial_state)
    except Exception:
        logger.exception("Chat preparation failed")
        yield f"data: {json.dumps({'type': 'error', 'code': 'GRAPH_FAILED', 'message': '服务暂时不可用，请稍后重试'})}\n\n"
        return

    # A rejected turn is not a business answer. Do not send the rejected
    # input to a model/judge or persist it as conversation/profile memory.
    if result.get("security_blocked"):
        if industrial_enabled() and (fresh or turn_key is not None):
            yield f"data: {json.dumps({'type': 'error', 'code': 'SECURITY_BLOCKED', 'message': result['reply']})}\n\n"
            return
        refusal = result["reply"]
        for i in range(0, len(refusal), 20):
            yield f"data: {json.dumps({'type': 'token', 'content': refusal[i : i + 20]})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'message_id': f'{session_id}-response', 'session_token': create_session_token(session_id)})}\n\n"
        return

    if research_intent:
        enabled = _managed_research_request_enabled()
        result = dict(result)
        result.update(
            {
                "reply": (
                    "这类多院校或多条件请求需要结构化招生研究和独立证据核验。"
                    "请使用下方结构化研究表单指定两所院校、专业与数据范围；提交后先等待运营人员审核。"
                    "该流程不会生成录取概率。"
                    if enabled
                    else "这类多院校或多条件请求需要结构化招生研究和独立证据核验，"
                    "但当前环境未启用该功能，因此不会生成未经核验的志愿建议。"
                    "请改为询问一个范围明确、可核验的事实问题。"
                ),
                "template_reply": "",
                "reply_source": "research_request",
                "structured_result": None,
                "rag_sources": [],
                "rag_state": None,
                "research_request_intent": True,
                "research_request_enabled": enabled,
            }
        )

    reply = result.get("reply") or ""
    is_template = result.get("reply_source") == "template"
    llm_configured = not industrial_enabled() and bool(str(load_llm_config().get("api_key", "")).strip())
    degraded = False
    degraded_message = ""

    if is_template and llm_configured:
        if result.get("knowledge_query") and not result.get("rag_evidence"):
            reply = "当前知识库未收录足够的政策或规则依据，无法验证该问题。请查阅当地教育考试院官方公告。"
            degraded = True
            degraded_message = "缺少可验证的知识依据，已停止模型生成"
        else:
            try:
                reply, degraded = await generate_chat_candidate(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Chat candidate generation failed")
                reply = result.get("template_reply") or result.get("reply") or ""
                degraded = True
            if degraded:
                degraded_message = "模型生成失败，已使用本地确定性模板"
    elif is_template:
        degraded = True
        degraded_message = "未配置 LLM Key，已使用基于结构化数据的确定性模板答复"

    rewrite_attempts = 0
    if is_template and llm_configured and not degraded and chat_rewrite_enabled():
        # Only repair a model answer rejected by *deterministic* rules.
        # Judge-only failures fail closed to the template; they cannot trigger
        # unbounded model calls or invent citations.
        reasons = _deterministic_gate_issues(result, reply)
        if reasons:
            rewrite_attempts = 1
            try:
                rewritten = await generate_chat_rewrite(result, reply, reasons)
                if rewritten:
                    reply = rewritten
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Chat rewrite orchestration failed")

    try:
        final, quality_degraded = await asyncio.to_thread(
            _finalize_chat_answer,
            result,
            reply,
            model_generated=is_template and llm_configured and not degraded,
            rewrite_attempts=rewrite_attempts,
            turn_version=turn_version,
            turn_key=turn_key,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        if turn_key is not None:
            try:
                receipt = await asyncio.to_thread(lookup_receipt, session_id, turn_key)
            except RequestKeyConflict:
                yield f"data: {json.dumps({'type': 'error', 'code': 'IDEMPOTENCY_CONFLICT', 'message': '请求键已用于其他请求'})}\n\n"
                return
            except Exception:
                receipt = None
            if receipt is not None:
                for event in _receipt_events(session_id, receipt):
                    yield f"data: {json.dumps(event)}\n\n"
                return
        logger.exception("Chat finalization/persistence failed")
        yield f"data: {json.dumps({'type': 'error', 'code': 'FINALIZATION_FAILED', 'message': '回答校验或保存失败，请稍后重试'})}\n\n"
        return

    if turn_key is not None:
        for event in _receipt_events(session_id, final["industrial_receipt"]):
            yield f"data: {json.dumps(event)}\n\n"
        return

    if final.get("research_request_intent"):
        yield f"data: {json.dumps({'type': 'research_intent', 'enabled': final['research_request_enabled']})}\n\n"

    if quality_degraded:
        degraded = True
        degraded_message = "模型回答未通过来源或质量规则检查，已使用本地模板"

    if final.get("slots"):
        yield f"data: {json.dumps({'type': 'slots', 'data': final['slots']})}\n\n"
    if final.get("emotion_state"):
        yield f"data: {json.dumps({'type': 'emotion', 'state': final['emotion_state']})}\n\n"
    if final.get("structured_result"):
        yield f"data: {json.dumps({'type': 'structured', 'result': final['structured_result']})}\n\n"
    if final.get("rag_state") == "RAG_ENABLED" and final.get("rag_sources"):
        yield f"data: {json.dumps({'type': 'sources', 'data': final['rag_sources'], 'has_evidence': True})}\n\n"
    elif final.get("rag_state") == "RAG_ENABLED":
        yield f"data: {json.dumps({'type': 'rag_status', 'state': 'NO_EVIDENCE', 'reason': final.get('rag_reason', '')})}\n\n"
    elif final.get("rag_state") in ("RAG_DISABLED", "RAG_ERROR"):
        yield f"data: {json.dumps({'type': 'rag_status', 'state': final['rag_state'], 'reason': final.get('rag_reason', '')})}\n\n"

    if degraded:
        yield f"data: {json.dumps({'type': 'degraded', 'message': degraded_message})}\n\n"
    if final.get("review_required"):
        yield f"data: {json.dumps({'type': 'review', 'status': 'required', 'reason_codes': final['review_reason_codes'], 'review_ticket_id': final.get('review_ticket_id'), 'message': '当前模型草稿未通过自动核验，已返回保守答复；如需实际决策请人工核验官方来源'})}\n\n"
    final_reply = final.get("reply") or ""
    for i in range(0, len(final_reply), 20):
        yield f"data: {json.dumps({'type': 'token', 'content': final_reply[i : i + 20]})}\n\n"

    done = {
        "type": "done",
        "session_id": session_id,
        "message_id": f"{session_id}-response",
        "session_token": create_session_token(session_id),
    }
    if industrial_enabled():
        done["idempotency_supported"] = True
    yield f"data: {json.dumps(done)}\n\n"


@router.post("/chat")
async def chat(
    request: ChatRequest,
    authorization: str | None = Header(None),
    x_user_memory_token: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
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

    from server.industrial_runtime import industrial_enabled

    turn_key = None
    if idempotency_key is not None and not industrial_enabled():
        raise HTTPException(status_code=409, detail="Idempotent chat is not enabled")
    if idempotency_key is not None:
        from server.services.industrial_chat_receipt import make_turn_key

        if authorization is None or x_user_memory_token is not None:
            raise HTTPException(status_code=400, detail="Keyed chat requires existing session without memory token")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key):
            raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
        try:
            turn_key = make_turn_key(idempotency_key, request.scene, request.message, request.slots)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid keyed request") from exc

    # Validate the independent vault credential before any SSE response.
    memory_items: list[dict] = []
    if x_user_memory_token is not None:
        from server.user_memory import MemoryDisabled, MemoryUnauthorized, list_memories

        try:
            memory_items = await asyncio.to_thread(list_memories, x_user_memory_token)
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
            fresh=authorization is None,
            turn_key=turn_key,
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
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        raise HTTPException(status_code=503, detail="Session feedback/highlight writes not released")

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
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        raise HTTPException(status_code=503, detail="Session feedback/highlight writes not released")

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
