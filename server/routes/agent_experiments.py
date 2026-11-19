"""Feature-gated, operator-only API for synthetic multi-step Agent research.

Does not replace the public /chat route. Does not accept user prompts or real
admissions data. The default disabled state requires explicit opt-in.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config.constants import DEMO_PROVINCES
from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
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


def _require_experiment_enabled() -> None:
    if (
        os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
        or os.getenv("APP_ENV", "development").lower() == "production"
    ):
        raise HTTPException(status_code=404, detail="Experiment not enabled")


@router.get("/example")
async def synthetic_research_example() -> dict:
    """Discover an explicitly fictional request from the same seeded dataset."""
    _require_experiment_enabled()
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "planner": "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM",
        "request": {
            "school_a": DEMO_SCHOOLS[0][0],
            "school_b": DEMO_SCHOOLS[1][0],
            "major": DEMO_MAJORS[0][0],
        },
    }


@router.post("/research")
async def synthetic_research(request: SyntheticResearchRequest) -> dict:
    """Run actual synthetic SQL data tools and return redacted Agent trace.

    This is a demonstration of runtime policy, NOT actual LLM autonomy.
    """
    _require_experiment_enabled()
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


class SyntheticDecisionCardRequest(BaseModel):
    """Only fictitious profiles may enter this experimental endpoint."""

    school_a: str = Field(..., min_length=3, max_length=80)
    school_b: str = Field(..., min_length=3, max_length=80)
    major: str = Field(..., min_length=3, max_length=80)
    province: str = Field(..., min_length=3, max_length=24)
    subject_type: str = Field(..., pattern=r"^(物理类|历史类)$")
    score: int = Field(..., ge=100, le=750)
    year: int = Field(..., ge=2020, le=2100)


@router.get("/decision-card/example")
async def synthetic_decision_card_example() -> dict:
    """Choose two fictional schools with actual SQL rows in the frozen demo."""
    _require_experiment_enabled()
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "planner": "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM",
        "request": {
            "school_a": DEMO_SCHOOLS[0][0],
            "school_b": DEMO_SCHOOLS[6][0],
            "major": DEMO_MAJORS[0][0],
            "province": "示例省·甲",
            "subject_type": "物理类",
            "score": 550,
            "year": DEMO_YEAR,
        },
    }


@router.post("/decision-card")
async def synthetic_decision_card(request: SyntheticDecisionCardRequest) -> dict:
    """Run real synthetic SQL evidence verification, not admissions prediction."""
    _require_experiment_enabled()
    if (
        request.school_a == request.school_b
        or request.school_a not in _ALLOWED_SCHOOLS
        or request.school_b not in _ALLOWED_SCHOOLS
        or request.major not in _ALLOWED_MAJORS
        or request.province not in DEMO_PROVINCES
        or request.year != DEMO_YEAR
    ):
        raise HTTPException(status_code=422, detail="Only frozen synthetic profile inputs are allowed")
    from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card

    profile = DecisionCardProfile(**request.model_dump())
    try:
        return await asyncio.wait_for(asyncio.to_thread(build_synthetic_decision_card, profile), timeout=12.0)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Synthetic decision-card deadline exceeded") from exc
    except Exception as exc:
        # Never leak SQL statements, source text or internal request state.
        raise HTTPException(status_code=503, detail="Synthetic decision-card service unavailable") from exc
