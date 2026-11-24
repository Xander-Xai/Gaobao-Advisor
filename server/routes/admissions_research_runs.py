"""Operator-only approved real-data research submission and inspection."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.admin.auth import AdminPrincipal, require_role
from server.agent.admissions_research_taskrun import (
    ResearchConflict,
    ResearchNotFound,
    configured_dataset,
    open_configured_journal,
)

router = APIRouter(
    prefix="/api/v1/admin/admissions-research",
    tags=["admissions-research"],
    dependencies=[Depends(require_role("operator"))],
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


class NewResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: ScopeRequest
    model_id: str = Field(pattern=r"^openai:[A-Za-z0-9_.-]{2,100}$")
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approval_ref: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,96}$")


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def store():
    try:
        return open_configured_journal()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Approved research journal unavailable") from exc


def translate(fn):
    try:
        return fn()
    except ResearchNotFound as exc:
        raise HTTPException(status_code=404, detail="No visible research run") from exc
    except ResearchConflict as exc:
        raise HTTPException(status_code=409, detail="Research state or approval conflict") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid research request") from exc


@router.post("/runs", status_code=201)
def create_run(request: NewResearch, principal: AdminPrincipal = Depends(require_role("operator"))):
    journal = store()
    spec = request.model_dump(exclude={"idempotency_key"})
    spec["dataset_ref"] = configured_dataset()
    return translate(lambda: journal.create(principal.username, request.idempotency_key, spec))


@router.get("/runs/{run_id}")
def get_run(run_id: str, principal: AdminPrincipal = Depends(require_role("operator"))):
    journal = store()
    return translate(lambda: journal.get(run_id, principal.username, admin=principal.role == "admin"))


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, request: Approval, principal: AdminPrincipal = Depends(require_role("admin"))):
    journal = store()
    return translate(lambda: journal.approve(run_id, principal.username, request.spec_sha256))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, principal: AdminPrincipal = Depends(require_role("operator"))):
    journal = store()
    return translate(lambda: journal.cancel(run_id, principal.username, admin=principal.role == "admin"))
