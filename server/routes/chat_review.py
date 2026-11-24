"""Privacy-safe operator triage of synthetic chat quality failures.

A triage acknowledgement NEVER certifies an admissions claim or changes an
already delivered answer. No original conversation, session token, prompt,
profile or rejected draft is exposed through this API.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from db.models import OperationAuditLog
from server.admin.auth import AdminPrincipal, require_role
from server.deps import get_db

router = APIRouter(
    prefix="/api/v1/admin/chat-reviews",
    tags=["chat-review"],
    dependencies=[Depends(require_role("operator"))],
)

DbSession = Annotated[Session, Depends(get_db)]
_TICKET_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class TriageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["acknowledged", "escalated"]
    # An operator-maintained synthetic review reference or official-source
    # URL; never place conversation text / student details here.
    review_reference: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")


def _details(row: OperationAuditLog) -> dict:
    try:
        data = json.loads(row.detail_json or "{}")
        codes = data.get("reason_codes") or []
        return {
            "reason_codes": [str(code)[:48] for code in codes[:10] if isinstance(code, str)],
            "delivered_reply_sha256": str(data.get("delivered_reply_sha256") or "")[:64],
            "rewrite_attempts": int(data.get("rewrite_attempts") or 0),
            "data_profile": "SYNTHETIC_DEMO",
        }
    except (ValueError, TypeError, AttributeError):
        return {"reason_codes": [], "data_profile": "SYNTHETIC_DEMO"}


@router.get("")
def list_chat_reviews(
    db: DbSession,
    principal: AdminPrincipal = Depends(require_role("operator")),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """List only anonymous reason codes and receipt digests."""
    tickets = (
        db.query(OperationAuditLog)
        .filter(
            OperationAuditLog.action == "chat.review_required",
            OperationAuditLog.resource_type == "chat_review",
        )
        .order_by(OperationAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    ids = [ticket.resource_id for ticket in tickets]
    resolutions = {}
    if ids:
        for row in (
            db.query(OperationAuditLog)
            .filter(
                OperationAuditLog.action == "chat.review_triaged",
                OperationAuditLog.resource_type == "chat_review",
                OperationAuditLog.resource_id.in_(ids),
            )
            .order_by(OperationAuditLog.id.desc())
            .all()
        ):
            resolutions.setdefault(row.resource_id, row.outcome)

    return {
        "items": [
            {
                "ticket_id": ticket.resource_id,
                "triage_status": resolutions.get(ticket.resource_id, "PENDING"),
                **_details(ticket),
            }
            for ticket in tickets
        ],
        "data_profile": "SYNTHETIC_DEMO",
        "factual_verification": "NOT_PERFORMED",
    }


@router.post("/{ticket_id}/triage")
def triage_chat_review(
    ticket_id: str,
    request: TriageReview,
    db: DbSession,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    """Append an audited triage event; never mark the answer factual/approved."""
    if not _TICKET_PATTERN.fullmatch(ticket_id):
        raise HTTPException(status_code=404, detail="Chat review ticket not found")
    ticket = (
        db.query(OperationAuditLog)
        .filter(
            OperationAuditLog.action == "chat.review_required",
            OperationAuditLog.resource_type == "chat_review",
            OperationAuditLog.resource_id == ticket_id,
        )
        .first()
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Chat review ticket not found")
    already = (
        db.query(OperationAuditLog)
        .filter(
            OperationAuditLog.action == "chat.review_triaged",
            OperationAuditLog.resource_type == "chat_review",
            OperationAuditLog.resource_id == ticket_id,
        )
        .first()
    )
    if already is not None:
        raise HTTPException(status_code=409, detail="Chat review already triaged")
    status = "ACKNOWLEDGED" if request.verdict == "acknowledged" else "ESCALATED"
    db.add(
        OperationAuditLog(
            actor=principal.username[:64],
            role=principal.role,
            action="chat.review_triaged",
            resource_type="chat_review",
            resource_id=ticket_id,
            outcome=status,
            detail_json=json.dumps(
                {
                    "review_reference": request.review_reference,
                    "verdict": request.verdict,
                    "factual_verification": "NOT_PERFORMED",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    )
    db.commit()
    return {
        "ticket_id": ticket_id,
        "triage_status": status,
        "factual_verification": "NOT_PERFORMED",
    }
