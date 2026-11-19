"""Feature-gated, operator-only API for synthetic multi-step Agent research.

Does not replace the public /chat route. Does not accept user prompts or real
admissions data. The default disabled state requires explicit opt-in.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS
from server.admin.auth import require_role

router = APIRouter(
    prefix="/api/v1/admin/agent-experiments",
    tags=["agent-experiments"],
    dependencies=[Depends(require_role("operator"))],
)
_ALLOWED_SCHOOLS = frozenset(row[0] for row in DEMO_SCHOOLS)
_ALLOWED_MAJORS = frozenset(row[0] for row in DEMO_MAJORS)


class SyntheticResearchRequest(BaseModel):
    school_a: str = Field(..., min_length=3, max_length=80)
    school_b: str = Field(..., min_length=3, max_length=80)
    major: str = Field(..., min_length=3, max_length=80)


@router.post("/research")
async def synthetic_research(request: SyntheticResearchRequest) -> dict:
    """Run actual synthetic SQL data tools and return redacted Agent trace.

    This is a demonstration of runtime policy, NOT actual LLM autonomy.
    """
    if (
        os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
        or os.getenv("APP_ENV", "development").lower() == "production"
    ):
        raise HTTPException(status_code=404, detail="Experiment not enabled")
    if (
        request.school_a not in _ALLOWED_SCHOOLS
        or request.school_b not in _ALLOWED_SCHOOLS
        or request.major not in _ALLOWED_MAJORS
        or request.school_a == request.school_b
    ):
        raise HTTPException(status_code=422, detail="Only distinct, predeclared synthetic entities are accepted")
    from server.agent.synthetic_research import research_synthetic_facts

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(research_synthetic_facts, request.school_a, request.school_b, request.major),
            timeout=12.0,
        )
    except TimeoutError as exc:
        # The thread is not forcibly killed. This is a response deadline, not
        # a hard sandbox/process timeout. Safe because tools are read-only.
        raise HTTPException(status_code=504, detail="Synthetic Agent research deadline exceeded") from exc
    except Exception as exc:
        # Never leak database paths, source text, or provider exceptions.
        raise HTTPException(status_code=503, detail="Synthetic Agent research temporarily unavailable") from exc
