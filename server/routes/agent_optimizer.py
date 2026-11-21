"""Admin-only, feature-gated synthetic experiment proposal and review endpoints.

No model calls are initiated. All input reports remain self-declared and the
simulated staging pointer is NOT consulted by production or /chat runtimes.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from server.admin.auth import AdminPrincipal, require_role
from server.agent.synthetic_optimization_registry import SyntheticOptimizationReviewStore
from server.agent.taskrun_store import TaskRunConflict, TaskRunNotFound
from server.routes.agent_taskruns import _store

router = APIRouter(
    prefix="/api/v1/admin/agent-optimizer",
    tags=["agent-optimizer"],
    dependencies=[Depends(require_role("admin"))],
)


class SyntheticOptimizationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = Field(min_length=2, max_length=5)


class SyntheticOptimizationSignoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["attest_reviewed", "reject"]


def _registry() -> SyntheticOptimizationReviewStore:
    # _store enforces current APP_ENV, feature flag, separate absolute journal.
    return SyntheticOptimizationReviewStore(_store())


@router.post("/proposals", status_code=201)
def submit_synthetic_optimization(
    request: SyntheticOptimizationSubmission,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    try:
        return _registry().submit(request.pairs, actor=principal.username)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="Invalid declared synthetic model report bundle") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Synthetic optimization journal unavailable or full") from exc


@router.get("/proposals/{proposal_id}")
def get_synthetic_optimization(
    proposal_id: str,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    try:
        return _registry().get(proposal_id)
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No such synthetic optimization proposal") from exc


@router.post("/proposals/{proposal_id}/review")
def review_synthetic_optimization(
    proposal_id: str,
    request: SyntheticOptimizationSignoff,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    try:
        return _registry().signoff(proposal_id, actor=principal.username, verdict=request.verdict)
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No such synthetic optimization proposal") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Synthetic optimization attestation rejected") from exc


@router.post("/proposals/{proposal_id}/simulate")
def simulate_synthetic_stage(
    proposal_id: str,
    principal: AdminPrincipal = Depends(require_role("admin")),
) -> dict:
    """Only update a local demonstration pointer; no rollout is performed."""
    try:
        return _registry().simulate_stage(proposal_id, actor=principal.username)
    except (TaskRunNotFound, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No such synthetic optimization proposal") from exc
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="Synthetic optimization staging simulation denied") from exc


@router.get("/demo-pointer")
def get_synthetic_demo_pointer(principal: AdminPrincipal = Depends(require_role("admin"))) -> dict:
    return _registry().demo_snapshot()


@router.post("/demo-pointer/rollback")
def rollback_synthetic_demo_pointer(principal: AdminPrincipal = Depends(require_role("admin"))) -> dict:
    try:
        return _registry().rollback(actor=principal.username)
    except TaskRunConflict as exc:
        raise HTTPException(status_code=409, detail="No synthetic demo pointer available to roll back") from exc
