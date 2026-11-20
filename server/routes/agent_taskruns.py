"""Operator TaskRun HTTP contract: create → admin approve → execute → inspect.

All actions are gated by existing admin auth, a default-off experiment flag,
and a separate local synthetic-only SQLite journal. The API intentionally
cannot launch a paid Deep Agents worker or expose arbitrary commands.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.admin.auth import AdminPrincipal, require_role
from server.agent.taskrun_store import (
    SyntheticTaskRunStore,
    TaskRunConflict,
    TaskRunNotFound,
)
from server.routes.agent_experiments import (
    SyntheticDecisionCardRequest,
    SyntheticPortfolioRequest,
    _require_experiment_enabled,
)

router = APIRouter(
    prefix="/api/v1/admin/agent-taskruns",
    tags=["agent-taskruns"],
    dependencies=[Depends(require_role("operator"))],
)


class NewSyntheticTaskRun(BaseModel):
    profile: SyntheticDecisionCardRequest
    engine: Literal["deterministic", "deepagents"] = "deterministic"
    idempotency_key: str = Field(..., min_length=8, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")


class NewSyntheticPortfolioTaskRun(BaseModel):
    """Explicitly non-model, operator-approved multi-candidate research."""

    model_config = ConfigDict(extra="forbid")

    portfolio: SyntheticPortfolioRequest
    idempotency_key: str = Field(..., min_length=8, max_length=96, pattern=r"^[A-Za-z0-9_.:-]+$")


class PortfolioReviewSignoff(BaseModel):
    """A signed-in administrator reviews a frozen *fictional* receipt hash."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["verified_historical_only", "reject"]
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _store() -> SyntheticTaskRunStore:
    _require_experiment_enabled()
    journal = os.getenv("SYNTHETIC_TASKRUN_JOURNAL", "")
    if not journal or not Path(journal).is_absolute() or not Path(journal).parent.is_dir():
        raise HTTPException(status_code=503, detail="Synthetic TaskRun journal is not configured")
    try:
        return SyntheticTaskRunStore(Path(journal))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Synthetic TaskRun journal unavailable") from exc


@router.post("/runs", status_code=201)
async def create_synthetic_taskrun(
    request: NewSyntheticTaskRun,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    store = _store()
    payload = {"data_profile": "SYNTHETIC_DEMO", "profile": request.profile.model_dump()}
    try:
        return store.create(payload, request.engine, principal.username, request.idempotency_key)
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unsupported synthetic task payload") from exc


@router.post("/portfolio-runs", status_code=201)
async def create_portfolio_taskrun(
    request: NewSyntheticPortfolioTaskRun,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    """Create only frozen synthetic SQL work, subject to maker-checker approval."""
    store = _store()
    payload = {"data_profile": "SYNTHETIC_DEMO", "portfolio": request.portfolio.model_dump()}
    try:
        return store.create(payload, "portfolio", principal.username, request.idempotency_key)
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Unsupported synthetic portfolio payload") from exc


@router.get("/runs/{run_id}")
async def get_synthetic_taskrun(
    run_id: str,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    store = _store()
    try:
        return store.get(run_id, requester=principal.username, admin=principal.role == "admin")
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No visible TaskRun") from exc


@router.get("/runs/{run_id}/review")
async def get_synthetic_portfolio_review(
    run_id: str,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    """Read the frozen SHA before a separate admin submits a review."""
    store = _store()
    try:
        return store.get_portfolio_review(run_id, requester=principal.username, admin=principal.role == "admin")
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No visible synthetic portfolio review") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Synthetic portfolio receipt is not reviewable") from exc


@router.post("/runs/{run_id}/review")
async def signoff_synthetic_portfolio_review(
    run_id: str,
    request: PortfolioReviewSignoff,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    """A review declaration, not a certification of real admissions advice."""
    store = _store()
    try:
        return store.signoff_portfolio_review(
            run_id,
            reviewer=principal.username,
            verdict=request.verdict,
            receipt_sha256=request.receipt_sha256,
        )
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Synthetic portfolio review not found") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Review rejected: stale receipt or separation-of-duties gate") from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_synthetic_taskrun(
    run_id: str,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    """Owner or administrator may revoke a task lease, not kill an OS process."""
    store = _store()
    try:
        return store.cancel(run_id, principal.username, admin=principal.role == "admin")
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No visible TaskRun") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Completed TaskRun cannot be cancelled") from exc


@router.post("/runs/{run_id}/approve")
async def approve_synthetic_taskrun(
    run_id: str,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    store = _store()
    try:
        return store.approve(run_id, principal.username)
    except TaskRunNotFound as exc:
        raise HTTPException(status_code=404, detail="TaskRun not found") from exc
    except (TaskRunConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Approval rejected by task state or maker-checker gate") from exc


@router.post("/runs/{run_id}/execute")
async def execute_synthetic_taskrun(
    run_id: str,
    principal: AdminPrincipal = Depends(require_role("operator")),
) -> dict:
    """Only fast deterministic tasks are callable through HTTP.

    Deep Agents runs require the separately invoked, explicitly authorized
    worker CLI so a browser click cannot trigger paid external model calls.
    """
    store = _store()
    try:
        item = store.get(run_id, requester=principal.username, admin=principal.role == "admin")
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No visible TaskRun") from exc
    if item["engine"] not in {"deterministic", "portfolio"}:
        raise HTTPException(status_code=409, detail="Deep Agents tasks require explicitly authorized worker")
    if item["status"] != "APPROVED":
        raise HTTPException(status_code=409, detail="TaskRun must be approved and claimable")
    from server.agent.taskrun_worker import execute_approved_run

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(execute_approved_run, store, run_id),
            timeout=15.0,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="TaskRun response deadline exceeded") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="TaskRun is not approved or is already leased") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="TaskRun execution is unavailable") from exc
