"""Report API routes — generate, retrieve, and export advisory reports.

Report generation reads the user's real conversation data (slots + recent AI
replies) from the database. It scans the last N rounds of AI replies to
accumulate suggestions, risks, and action items — not just the last reply,
since recommendations are often spread across multiple conversation turns.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel

from server.auth import bearer_token, require_session_auth, verify_session_token
from server.report.cover import CoverGenerator
from server.report.exporter import ReportExporter
from server.report.generator import ReportGenerator
from server.report.storage import ReportStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["report"])

_storage = ReportStorage()


def _load_owned_report(report_id: str, authorization: str | None):
    """Load a report and prove the caller owns the session that produced it.

    Ordering matters: require a credential *before* touching storage, then
    distinguish "not found" from "not yours". A caller can only reach a report
    by presenting the HMAC token bound to its session_id.
    """
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        raise HTTPException(status_code=503, detail="Evidence-bound report export not released")
    token = bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing session token")
    try:
        report = _storage.load(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report id") from None
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if not verify_session_token(report.session_id, token):
        raise HTTPException(status_code=401, detail="Invalid or expired session token")
    return report


# How many recent AI replies to scan for content extraction.
# Keeps parsing fast even for long conversations while covering enough
# rounds to capture multi-turn recommendations.
_MAX_RECENT_ROUNDS = 10


class GenerateRequest(BaseModel):
    """Request body for report generation."""

    session_id: str
    student_name: str | None = None


class GenerateResponse(BaseModel):
    """Response body for report generation."""

    report_id: str
    status: str
    message: str


# ── Data loading ───────────────────────────────────────────────────────


def _load_session_slots(session_id: str) -> dict:
    """Load slot data from the conversation database.

    Returns the raw slots dict (may be nested {"value": ..., "filled": True}
    format or flat string format), or empty dict if not found.
    """
    from db.crud import load_conversation_slots
    from db.database import get_session

    db = get_session()
    try:
        slots = load_conversation_slots(db, session_id)
        return slots or {}
    except Exception:
        logger.warning("Failed to load slots for session %s", session_id)
        return {}
    finally:
        db.close()


def _load_recent_ai_replies(session_id: str, max_rounds: int = _MAX_RECENT_ROUNDS) -> list[str]:
    """Load the most recent AI assistant replies from conversation history.

    Returns up to `max_rounds` assistant messages, ordered from oldest to
    newest. This allows content extraction to accumulate recommendations
    across multiple turns rather than relying on just the last reply.

    Args:
        session_id: The conversation session ID.
        max_rounds: Maximum number of recent AI replies to return.
    """
    from db.crud import load_conversation_history
    from db.database import get_session

    db = get_session()
    try:
        messages = load_conversation_history(db, session_id)
        # Collect assistant messages from newest to oldest
        ai_replies: list[str] = []
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                ai_replies.append(msg["content"])
                if len(ai_replies) >= max_rounds:
                    break
        # Return in chronological order (oldest first)
        ai_replies.reverse()
        return ai_replies
    except Exception:
        logger.warning("Failed to load history for session %s", session_id)
        return []
    finally:
        db.close()


# ── Slot-based extraction (fast, structured) ──────────────────────────


def _parse_facts_from_slots(slots: dict) -> list[str]:
    """Extract factual statements from slot data."""
    facts: list[str] = []
    slot_labels = {
        "province": "省份",
        "score": "分数",
        "score_rank": "分数",
        "subject": "选科",
        "interest": "专业意向",
        "region": "地域偏好",
        "family": "家庭背景",
        "goal": "核心诉求",
    }
    for key, label in slot_labels.items():
        val = slots.get(key, "")
        if isinstance(val, dict):
            val = val.get("value", "")
        if val:
            facts.append(f"{label}：{val}")
    return facts


def _build_summary(slots: dict) -> str:
    """Build a one-line summary from slot data."""
    parts: list[str] = []

    def _get(key: str) -> str:
        val = slots.get(key, "")
        if isinstance(val, dict):
            val = val.get("value", "")
        return str(val) if val else ""

    province = _get("province")
    score = _get("score") or _get("score_rank")
    subject = _get("subject")
    interest = _get("interest")

    if province:
        parts.append(province)
    if score:
        parts.append(f"{score}分")
    if subject:
        parts.append(subject)
    if interest:
        parts.append(f"意向{interest}")

    if parts:
        return "，".join(parts) + "，志愿规划分析报告。"
    return "高考志愿填报分析报告"


# ── Reply-based extraction (multi-turn, deduplicated) ─────────────────


def _clean_line(line: str) -> str:
    """Strip markdown/bullet/numbering prefix from a line."""
    return re.sub(r"^[\d\-\*•\.、]+\s*", "", line.strip())


def _is_content_line(line: str) -> bool:
    """Check if a line has meaningful content (not just formatting/headers)."""
    stripped = line.strip()
    return bool(stripped) and len(stripped) > 2 and not stripped.startswith("```")


def _parse_suggestions_from_replies(replies: list[str]) -> list[str]:
    """Extract school/major suggestions across multiple AI replies, deduplicated.

    Scans each reply for lines containing recommendation markers, then
    deduplicates by normalizing the text. Keeps first occurrence (earliest
    mention), preserves chronological order.
    """
    seen_normalized: set[str] = set()
    suggestions: list[str] = []

    for reply in replies:
        for line in reply.split("\n"):
            stripped = line.strip()
            if not _is_content_line(stripped):
                continue

            is_suggestion = any(marker in stripped for marker in ["推荐", "冲刺", "稳妥", "保底"]) or re.match(
                r"^\d+[\.\、]\s*.+大[学院]", stripped
            )

            if is_suggestion:
                clean = _clean_line(stripped)
                if not clean or len(clean) <= 2:
                    continue
                # Normalize for dedup: strip whitespace + punctuation, lowercase
                normalized = re.sub(r"[《》【】\s]", "", clean)
                if normalized not in seen_normalized:
                    seen_normalized.add(normalized)
                    suggestions.append(clean)

    return suggestions[:15] if suggestions else []


def _parse_risks_from_replies(replies: list[str]) -> list[str]:
    """Extract risk warnings across multiple AI replies, deduplicated.

    Scans each reply for risk sections and inline risk markers.
    Deduplicates to avoid repeating the same warning from different turns.
    """
    seen_normalized: set[str] = set()
    risks: list[str] = []

    for reply in replies:
        in_risk_section = False
        for line in reply.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # Detect risk section header or inline risk
            is_risk_header = any(marker in stripped for marker in ["风险", "注意", "提醒", "⚠"]) and len(stripped) < 20
            is_risk_inline = any(marker in stripped for marker in ["风险", "注意", "提醒", "⚠"]) and len(stripped) >= 20

            if is_risk_header:
                in_risk_section = True
                continue

            if is_risk_inline:
                in_risk_section = True

            if in_risk_section:
                # Stop if we hit a different section
                if any(marker in stripped for marker in ["建议行动", "推荐院校", "📌", "📋", "🎯"]):
                    in_risk_section = False
                    continue

                clean = _clean_line(stripped)
                if clean and len(clean) > 3:
                    normalized = re.sub(r"[，。、！？\s]", "", clean)
                    if normalized not in seen_normalized:
                        seen_normalized.add(normalized)
                        risks.append(clean)

    return risks if risks else ["数据有限，建议以官方最新信息为准"]


def _parse_next_actions_from_replies(replies: list[str]) -> list[str]:
    """Extract next action items across multiple AI replies, deduplicated.

    Scans each reply for action sections. Deduplicates and keeps the
    most recent phrasing of semantically identical actions.
    """
    seen_normalized: set[str] = set()
    actions: list[str] = []

    for reply in replies:
        in_action_section = False
        for line in reply.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # Detect action section header or inline action
            is_action_header = (
                any(marker in stripped for marker in ["建议行动", "下一步", "行动建议", "📌"]) and len(stripped) < 20
            )
            is_action_inline = (
                any(marker in stripped for marker in ["建议行动", "下一步", "行动建议", "📌"]) and len(stripped) >= 20
            )

            if is_action_header:
                in_action_section = True
                continue

            if is_action_inline:
                in_action_section = True

            if in_action_section:
                # Stop at next section
                if any(marker in stripped for marker in ["风险提示", "推荐院校", "⚠", "📋", "🎯"]):
                    in_action_section = False
                    continue

                clean = _clean_line(stripped)
                if clean and len(clean) > 3:
                    normalized = re.sub(r"[，。、！？\s]", "", clean)
                    if normalized not in seen_normalized:
                        seen_normalized.add(normalized)
                        actions.append(clean)

    return actions if actions else ["补充省份、分数、选科等信息以获得更精准推荐"]


# ── Report generation endpoint ─────────────────────────────────────────


@router.post("/report/generate", response_model=GenerateResponse)
async def generate_report(body: GenerateRequest, authorization: str | None = Header(None)):
    """Generate a report from the user's real conversation data.

    Reads slots (province, score, subject, interest) for the student
    profile, then scans recent AI replies to accumulate suggestions,
    risks, and action items across multiple turns — not just the last one.

    Requires the Bearer token bound to ``body.session_id`` so a caller cannot
    read another session's conversation by naming its id.
    """
    session_id = body.session_id
    require_session_auth(session_id, authorization)
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        raise HTTPException(status_code=503, detail="Evidence-bound report generation not released")

    # 1. Load real data from conversation database
    slots = _load_session_slots(session_id)
    recent_replies = _load_recent_ai_replies(session_id)

    # 2. Extract structured content
    #    - Student profile from slots (fast, structured, always accurate)
    #    - Recommendations from recent AI replies (multi-turn, deduplicated)
    facts = _parse_facts_from_slots(slots)
    suggestions = _parse_suggestions_from_replies(recent_replies)
    risks = _parse_risks_from_replies(recent_replies)
    next_actions = _parse_next_actions_from_replies(recent_replies)
    summary = _build_summary(slots)

    # 3. Build the Report from real data
    report = ReportGenerator.from_card(
        card=None,
        session_id=session_id,
        slots=slots,
        student_name=body.student_name,
    )

    # Populate content from parsed conversation data
    report.summary = summary
    report.facts = facts
    report.suggestions = suggestions
    report.risks = risks
    report.next_actions = next_actions

    # Calculate confidence based on profile completeness
    filled = sum(1 for f in ("province", "score", "subject", "interest") if slots.get(f))
    report.confidence = round(filled / 4, 2) if filled else 0.0

    # 4. Save and return
    _storage.save(report)
    logger.info(
        "Report generated: %s for session %s (confidence=%.0f%%, facts=%d, suggestions=%d, scanned_rounds=%d)",
        report.id,
        session_id,
        report.confidence * 100,
        len(facts),
        len(suggestions),
        len(recent_replies),
    )

    return GenerateResponse(
        report_id=report.id,
        status="completed",
        message="报告已生成",
    )


@router.get("/report/{report_id}")
async def get_report(report_id: str, authorization: str | None = Header(None)):
    """Return report data as a JSON dict (owner only)."""
    report = _load_owned_report(report_id, authorization)
    return report.to_dict()


@router.get("/report/{report_id}/html")
async def get_report_html(report_id: str, authorization: str | None = Header(None)):
    """Return the full HTML report page (owner only)."""
    report = _load_owned_report(report_id, authorization)
    html = ReportExporter.to_html(report)
    return Response(content=html, media_type="text/html")


@router.get("/report/{report_id}/cover.svg")
async def get_report_cover_svg(report_id: str, authorization: str | None = Header(None)):
    """Return the SVG cover image for a report (owner only)."""
    report = _load_owned_report(report_id, authorization)
    svg = CoverGenerator.generate_svg(report)
    return Response(content=svg, media_type="image/svg+xml")
