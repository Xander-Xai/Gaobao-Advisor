"""Profile endpoints — user profiling via soul query engine.

Endpoints:
- GET  /api/v1/profile/{session_id}     → current profile + completeness
- PUT  /api/v1/profile/{session_id}     → update single field
- GET  /api/v1/profile/{session_id}/next-question → next question
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from server.auth import require_session_auth
from server.deps import get_soul_query_engine
from server.soul_query import QueryState
from server.user_profile import load_profile, save_profile

router = APIRouter(prefix="/api/v1", tags=["profile"])


def _require_auth(session_id: str, authorization: str | None = None) -> None:
    """Validate session ownership via Bearer token. Raises 401 on failure."""
    require_session_auth(session_id, authorization)


class ProfileUpdateRequest(BaseModel):
    field: str = Field(..., min_length=1, max_length=32)
    value: str = Field(..., min_length=1, max_length=200)


class ProfileResponse(BaseModel):
    session_id: str
    profile: dict
    is_complete: bool
    missing_fields: list[str]


class NextQuestionResponse(BaseModel):
    session_id: str
    question: str | None
    round_count: int
    is_complete: bool


class SkipFieldRequest(BaseModel):
    field: str = Field(..., min_length=1, max_length=32)


@router.get("/profile/{session_id}", response_model=ProfileResponse)
async def get_profile(session_id: str, authorization: str | None = Header(None)):
    """Get current user profile and completeness status."""
    _require_auth(session_id, authorization)
    profile = load_profile(session_id)
    return ProfileResponse(
        session_id=session_id,
        profile=profile.to_dict(),
        is_complete=profile.is_required_complete(),
        missing_fields=profile.missing_required_fields(),
    )


@router.put("/profile/{session_id}", response_model=ProfileResponse)
async def update_profile_field(session_id: str, req: ProfileUpdateRequest, authorization: str | None = Header(None)):
    """Update a single profile field."""
    _require_auth(session_id, authorization)
    profile = load_profile(session_id)
    valid_fields = {"province", "score", "subject", "interest", "region", "family", "goal"}
    if req.field not in valid_fields:
        raise HTTPException(status_code=400, detail=f"Invalid field: {req.field}")

    # Parse numeric fields
    if req.field == "score":
        try:
            val = int(req.value)
            if not (100 <= val <= 750):
                raise ValueError
            profile.score = val
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Score must be integer between 100 and 750") from None
    else:
        setattr(profile, req.field, req.value)

    save_profile(session_id, profile)
    return ProfileResponse(
        session_id=session_id,
        profile=profile.to_dict(),
        is_complete=profile.is_required_complete(),
        missing_fields=profile.missing_required_fields(),
    )


@router.get("/profile/{session_id}/next-question", response_model=NextQuestionResponse)
async def get_next_question(session_id: str, authorization: str | None = Header(None)):
    """Get the next soul query question for this session."""
    _require_auth(session_id, authorization)
    engine = get_soul_query_engine()
    profile = load_profile(session_id)
    query_state = _load_query_state(session_id)

    question = engine.get_next_question(profile, query_state)
    _save_query_state(session_id, query_state)

    return NextQuestionResponse(
        session_id=session_id,
        question=question,
        round_count=query_state.round_count,
        is_complete=engine.is_query_complete(profile),
    )


@router.post("/profile/{session_id}/skip")
async def skip_field(session_id: str, req: SkipFieldRequest, authorization: str | None = Header(None)):
    """Skip an optional field (uses default value)."""
    _require_auth(session_id, authorization)
    engine = get_soul_query_engine()
    query_state = _load_query_state(session_id)
    engine.handle_skip(query_state, req.field)
    _save_query_state(session_id, query_state)
    return {"status": "skipped", "field": req.field}


# ── Query state persistence (stored in session slots) ─────────────


def _query_state_key(session_id: str) -> str:
    return f"query_state:{session_id}"


def _load_query_state(session_id: str) -> QueryState:
    """Load QueryState from the database."""
    from db.crud import load_conversation_slots
    from db.database import get_session

    db = get_session()
    try:
        slot_data = load_conversation_slots(db, session_id) or {}
        qs = slot_data.get("_query_state", {})
        return QueryState(
            round_count=qs.get("round_count", 0),
            asked_fields=qs.get("asked_fields", []),
            skipped_fields=qs.get("skipped_fields", []),
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("Failed to load query state: %s", exc)
        return QueryState()
    finally:
        db.close()


def _save_query_state(session_id: str, state: QueryState) -> None:
    """Merge QueryState into session slots without deleting profile data."""
    from db.crud import get_or_create_conversation, load_conversation_slots, save_slots
    from db.database import get_session

    db = get_session()
    try:
        get_or_create_conversation(db, session_id)
        slot_data = load_conversation_slots(db, session_id) or {}
        slot_data["_query_state"] = {
            "round_count": state.round_count,
            "asked_fields": state.asked_fields,
            "skipped_fields": state.skipped_fields,
        }
        save_slots(db, session_id, slot_data)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("Failed to save query state: %s", exc)
    finally:
        db.close()
