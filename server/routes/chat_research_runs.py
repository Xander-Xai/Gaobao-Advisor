"""User-owned requests for the existing, separately approved research lifecycle."""

import hashlib
import json
import os
import sqlite3
import time
from asyncio import sleep

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from db.models import Major, School
from server.agent.admissions_research_taskrun import (
    ResearchConflict,
    ResearchNotFound,
    configured_dataset,
    open_configured_journal,
    render_aggregate_report,
)
from server.auth import require_session_auth
from server.deps import get_db


class PrivateResearchRequestRoute(APIRoute):
    """Keep private session task data and errors out of shared caches."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            headers = {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(status_code=422, detail="Invalid research request", headers=headers) from None
            except HTTPException as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers={**(exc.headers or {}), **headers},
                ) from None
            except (OSError, sqlite3.Error, SQLAlchemyError):
                raise HTTPException(status_code=503, detail="Research storage unavailable", headers=headers) from None
            response.headers.update(headers)
            return response

        return private_handler


router = APIRouter(
    prefix="/api/v1/chat/research-runs",
    tags=["chat-research"],
    route_class=PrivateResearchRequestRoute,
)


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    school_ids: list[int] = Field(min_length=2, max_length=2)
    major_id: int = Field(gt=0)
    province: str = Field(min_length=1, max_length=20)
    subject_type: str = Field(min_length=1, max_length=10)
    batch: str = Field(min_length=1, max_length=20)
    year_from: int
    year_to: int


class NewChatResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=4, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,96}$")
    scope: ScopeRequest


def _enabled() -> bool:
    return (
        os.getenv("APP_ENV", "development").strip().lower() != "production"
        and os.getenv("GAOBAO_RUNTIME_PROFILE", "").strip().lower() != "industrial"
        and os.getenv("ADMISSIONS_RESEARCH_ENABLED", "").strip().lower() == "true"
        and os.getenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", "").strip().lower() == "true"
    )


def _journal():
    if not _enabled():
        raise HTTPException(status_code=404, detail="Resource not found")
    if any(
        not os.getenv(name, "").strip()
        for name in (
            "ADMISSIONS_RESEARCH_MODEL_ID",
            "ADMISSIONS_RESEARCH_IMAGE_ID",
            "ADMISSIONS_RESEARCH_APPROVAL_REF",
        )
    ):
        raise HTTPException(status_code=503, detail="Approved research policy unavailable")
    try:
        configured_dataset()
        return open_configured_journal()
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="Approved research service unavailable") from None


def _owner(session_id: str) -> str:
    return "session_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _translate(operation):
    try:
        return operation()
    except ResearchNotFound:
        raise HTTPException(status_code=404, detail="No visible research run") from None
    except ResearchConflict:
        raise HTTPException(status_code=409, detail="Research state or request conflict") from None
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid research request") from None


def _progress_stage(status: str) -> str:
    return {
        "PENDING_APPROVAL": "awaiting_operator_approval",
        "APPROVED": "awaiting_authorized_worker",
        "RUNNING": "deep_agents_execution",
        "SUCCEEDED": "verified_report_ready",
        "NEEDS_REVIEW": "partial_result_requires_review",
        "FAILED": "execution_failed",
        "CANCELLED": "cancelled",
        "EXPIRED": "expired_without_replay",
    }[status]


@router.post("", status_code=201)
def create_chat_research(request: NewChatResearch, authorization: str | None = Header(None)):
    require_session_auth(request.session_id, authorization)
    journal = _journal()
    spec = {
        "scope": request.scope.model_dump(),
        "model_id": os.environ["ADMISSIONS_RESEARCH_MODEL_ID"],
        "dataset_ref": configured_dataset(),
        "image_id": os.environ["ADMISSIONS_RESEARCH_IMAGE_ID"],
        "approval_ref": os.environ["ADMISSIONS_RESEARCH_APPROVAL_REF"],
    }
    return _translate(lambda: journal.create(_owner(request.session_id), request.idempotency_key, spec))


@router.get("/options")
def chat_research_options(
    session_id: str,
    school_query: str = Query("", max_length=100),
    major_query: str = Query("", max_length=100),
    authorization: str | None = Header(None),
    db=Depends(get_db),
):
    require_session_auth(session_id, authorization)
    _journal()
    schools = db.query(School).filter(or_(School.special_type.is_(None), School.special_type != "SYNTHETIC_DEMO_DATA"))
    majors = db.query(Major)
    if school_query.strip():
        schools = schools.filter(School.name.ilike(f"%{school_query.strip()}%"))
    if major_query.strip():
        majors = majors.filter(Major.name.ilike(f"%{major_query.strip()}%"))
    return {
        "schools": [{"id": row.id, "name": row.name} for row in schools.order_by(School.name, School.id).limit(50)],
        "majors": [{"id": row.id, "name": row.name} for row in majors.order_by(Major.name, Major.id).limit(50)],
    }


@router.get("/{run_id}")
def get_chat_research(run_id: str, session_id: str, authorization: str | None = Header(None)):
    require_session_auth(session_id, authorization)
    journal = _journal()
    return _translate(lambda: journal.get(run_id, _owner(session_id)))


@router.post("/{run_id}/cancel")
def cancel_chat_research(run_id: str, session_id: str, authorization: str | None = Header(None)):
    require_session_auth(session_id, authorization)
    journal = _journal()
    return _translate(lambda: journal.cancel(run_id, _owner(session_id)))


@router.get("/{run_id}/report", response_class=PlainTextResponse)
def get_chat_research_report(run_id: str, session_id: str, authorization: str | None = Header(None)):
    require_session_auth(session_id, authorization)
    journal = _journal()
    item = _translate(lambda: journal.get(run_id, _owner(session_id)))
    report = _translate(lambda: render_aggregate_report(item))
    return PlainTextResponse(report, headers={"Cache-Control": "private, no-store"})


@router.get("/{run_id}/events")
async def chat_research_events(run_id: str, session_id: str, authorization: str | None = Header(None)):
    require_session_auth(session_id, authorization)
    journal = _journal()
    owner = _owner(session_id)
    initial = _translate(lambda: journal.get(run_id, owner))
    terminal = {"SUCCEEDED", "NEEDS_REVIEW", "FAILED", "CANCELLED", "EXPIRED"}

    async def stream():
        previous = None
        deadline = time.monotonic() + 300
        heartbeat = time.monotonic()
        item = initial
        while True:
            status = item["status"]
            payload = {
                "type": "progress",
                "run_id": run_id,
                "status": status,
                "stage": _progress_stage(status),
                "terminal": status in terminal,
                "updated_at": item["updated_at"],
                "failure_code": item["failure_code"],
            }
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != previous:
                yield f"event: progress\ndata: {encoded}\n\n"
                previous = encoded
            if status in terminal or time.monotonic() >= deadline:
                break
            await sleep(2)
            if time.monotonic() - heartbeat >= 15:
                yield ": keepalive\n\n"
                heartbeat = time.monotonic()
            try:
                item = journal.get(run_id, owner)
            except (ResearchConflict, ResearchNotFound):
                yield 'event: error\ndata: {"code":"RESEARCH_STATE_UNAVAILABLE"}\n\n'
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )
