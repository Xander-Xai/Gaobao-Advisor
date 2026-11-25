"""Regression tests for session-slot / query-state persistence.

Two related data-loss defects, both proved against unmodified `master`:

1. ``routes/profile.py::_save_query_state`` replaced ``Conversation.slots_json``
   wholesale with a dict containing only ``_query_state``. Every call to
   ``POST /api/v1/profile/{id}/next-question`` or ``POST /api/v1/profile/{id}/skip``
   therefore erased all collected profile slots (province / score / subject /
   interest / ...) and blanked the flattened ``province`` / ``score_rank``
   columns. The advisor forgot everything the user had told it.

2. ``graph/nodes/memory.py::memory_node`` wrote ``state["slots"]`` verbatim.
   ``_query_state`` is a *top-level* graph key, so the round counter and the
   already-asked field list produced by ``question_generate_node`` were dropped
   at the end of every chat turn and never persisted.

Both are fixed by merging instead of replacing. Each test below uses its own
throw-away SQLite file; ``data/gaokao.db`` is never read or written.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database
from db.crud import load_conversation_slots, save_slots
from db.models import Base, Conversation
from server.graph.nodes.memory import memory_node
from server.routes.profile import _load_query_state, _save_query_state
from server.soul_query import QueryState

PROFILE_SLOTS = {
    "province": {"value": "山东", "filled": True},
    "score_rank": {"value": "620分", "filled": True},
    "subject": {"value": "物理", "filled": True},
    "interest": {"value": "想学计算机", "filled": True},
}


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    """Point every DB helper at an isolated per-test SQLite file."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'slots.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)

    from db import crud

    monkeypatch.setattr(db.database, "get_session", factory)
    monkeypatch.setattr(db.database, "SessionLocal", factory)
    monkeypatch.setattr(crud, "get_session", factory, raising=False)
    yield factory
    engine.dispose()


def _seed_profile(factory, session_id):
    db = factory()
    try:
        save_slots(db, session_id, dict(PROFILE_SLOTS))
        db.commit()
    finally:
        db.close()


def _stored(factory, session_id):
    db = factory()
    try:
        return load_conversation_slots(db, session_id) or {}
    finally:
        db.close()


def _flattened(factory, session_id):
    db = factory()
    try:
        conv = db.query(Conversation).filter(Conversation.session_id == session_id).one()
        return conv.province, conv.score_rank
    finally:
        db.close()


# ── defect 1: _save_query_state destroyed the profile ────────────────────────


def test_save_query_state_preserves_profile_slots(session_factory):
    """Writing query state must not erase the collected profile."""
    sid = "slot-preserve-1"
    _seed_profile(session_factory, sid)

    _save_query_state(sid, QueryState(round_count=2, asked_fields=["province", "score"]))

    stored = _stored(session_factory, sid)
    for key, value in PROFILE_SLOTS.items():
        assert stored[key] == value, f"slot {key} was lost"
    assert stored["_query_state"]["round_count"] == 2


def test_save_query_state_keeps_flattened_columns(session_factory):
    """The denormalised province/score_rank columns must survive too."""
    sid = "slot-preserve-2"
    _seed_profile(session_factory, sid)
    before = _flattened(session_factory, sid)
    assert before == ("山东", "620分")

    _save_query_state(sid, QueryState(round_count=1))

    assert _flattened(session_factory, sid) == ("山东", "620分")


def test_save_query_state_round_trips(session_factory):
    """Query state is still readable after being merged in."""
    sid = "slot-preserve-3"
    _seed_profile(session_factory, sid)
    wanted = QueryState(round_count=4, asked_fields=["province", "score", "subject"], skipped_fields=["goal"])

    _save_query_state(sid, wanted)

    assert _load_query_state(sid) == wanted


def test_save_query_state_updates_previous_round(session_factory):
    """Consecutive calls must update, not accumulate, `_query_state`."""
    sid = "slot-preserve-4"
    _seed_profile(session_factory, sid)

    _save_query_state(sid, QueryState(round_count=1, asked_fields=["province"]))
    _save_query_state(sid, QueryState(round_count=2, asked_fields=["province", "score"]))

    stored = _stored(session_factory, sid)
    assert stored["_query_state"]["round_count"] == 2
    assert stored["_query_state"]["asked_fields"] == ["province", "score"]
    assert len(stored["_query_state"]["asked_fields"]) == 2


def test_save_query_state_on_fresh_session(session_factory):
    """A brand new session has no slots to preserve — it must not crash."""
    sid = "slot-preserve-5"
    _save_query_state(sid, QueryState(round_count=1, asked_fields=["province"]))
    assert _stored(session_factory, sid)["_query_state"]["round_count"] == 1


# ── defect 2: memory_node dropped _query_state ───────────────────────────────


def test_memory_node_persists_query_state(session_factory):
    """A chat turn must persist the graph's `_query_state`."""
    sid = "memory-query-1"
    state = {
        "session_id": sid,
        "slots": dict(PROFILE_SLOTS),
        "_query_state": {
            "round_count": 3,
            "asked_fields": ["province", "score"],
            "skipped_fields": [],
        },
        "input_text": "山东620分",
        "reply": "你是山东考生吗？",
    }

    memory_node(dict(state))

    stored = _stored(session_factory, sid)
    assert stored["_query_state"] == state["_query_state"]
    for key, value in PROFILE_SLOTS.items():
        assert stored[key] == value


def test_memory_node_without_query_state_is_unchanged(session_factory):
    """Turns with no `_query_state` keep persisting slots only — no stray key."""
    sid = "memory-query-2"
    memory_node(
        {
            "session_id": sid,
            "slots": dict(PROFILE_SLOTS),
            "input_text": "你好",
            "reply": "请问你哪个省的？",
        }
    )
    stored = _stored(session_factory, sid)
    assert "_query_state" not in stored
    assert stored["province"] == PROFILE_SLOTS["province"]


def test_memory_node_does_not_mutate_caller_state(session_factory):
    """`state["slots"]` belongs to the caller; memory_node must copy, not edit."""
    sid = "memory-query-3"
    slots = dict(PROFILE_SLOTS)
    state = {
        "session_id": sid,
        "slots": slots,
        "_query_state": {"round_count": 1, "asked_fields": ["province"], "skipped_fields": []},
        "input_text": "x",
        "reply": "y",
    }

    memory_node(state)

    assert "_query_state" not in slots


def test_query_state_survives_a_full_two_turn_conversation(session_factory):
    """The reported end-to-end symptom: state must not reset between turns."""
    sid = "memory-query-4"
    _seed_profile(session_factory, sid)

    # turn 1 — advisor asks the next question
    memory_node(
        {
            "session_id": sid,
            "slots": dict(PROFILE_SLOTS),
            "_query_state": {"round_count": 1, "asked_fields": ["province"], "skipped_fields": []},
            "input_text": "山东620分",
            "reply": "你选的是物理吗？",
        }
    )
    # turn 2 — question_generate_node advances the round counter
    memory_node(
        {
            "session_id": sid,
            "slots": dict(PROFILE_SLOTS),
            "_query_state": {"round_count": 2, "asked_fields": ["province", "subject"], "skipped_fields": []},
            "input_text": "对，物理",
            "reply": "那你倾向哪个专业？",
        }
    )

    stored = _stored(session_factory, sid)
    assert stored["_query_state"]["round_count"] == 2
    assert stored["_query_state"]["asked_fields"] == ["province", "subject"]
    assert stored["province"] == PROFILE_SLOTS["province"]


@pytest.mark.parametrize("stored", ["{broken", "[]", "null", "123"])
def test_industrial_corrupt_session_slots_fail_closed(session_factory, monkeypatch, stored):
    from server.routes.chat import _load_chat_state

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    with session_factory() as db:
        db.add(Conversation(session_id="industrial-corrupt", slots_json=stored))
        db.commit()
    with pytest.raises(RuntimeError, match="Session storage unavailable"):
        _load_chat_state("industrial-corrupt", {"score": 600})


def test_industrial_session_loading_does_not_coerce_raw_score(session_factory, monkeypatch):
    from server.routes.chat import _load_chat_state
    from server.services.industrial_admissions import exact_score

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    with session_factory() as db:
        save_slots(db, "industrial-score", {"score_rank": "1600分"})
    slots, _ = _load_chat_state("industrial-score", None)
    assert slots["score_rank"] == "1600分"
    assert "score" not in slots
    assert exact_score(slots) is None
