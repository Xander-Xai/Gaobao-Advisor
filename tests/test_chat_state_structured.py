"""Regression tests for chat state persistence and structured recommendations.

These pin the defects fixed in Issue #54:

* stored slot records and flat graph slots must round-trip;
* explicit chat corrections override the old value, while a passing mention
  does not;
* an authenticated chat turn loads the persisted profile/query state;
* the offline template lists real matched schools;
* the structured card carries traceable sources.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.database
from db.crud import load_conversation_slots, save_slots
from db.database import Base
from server.auth import create_session_token
from server.graph.nodes.extract import slot_extract_node
from server.graph.nodes.render import render_reply_node
from server.graph.nodes.structure import structure_output_node
from server.main import app
from server.user_profile import load_profile
from slots.persistence import flatten_slots, normalize_slots

PROFILE = {
    "province": "河北",
    "score_rank": "600分",
    "subject": "物理",
    "interest": "想学计算机",
}


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-state.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(db.database, "SessionLocal", factory)
    monkeypatch.setattr(db.database, "get_session", factory)
    monkeypatch.setattr("server.graph.nodes.memory.get_session", factory)
    yield factory
    engine.dispose()


def _seed(factory, session_id: str, flat: dict, query_state: dict | None = None) -> None:
    db = factory()
    try:
        save_slots(
            db,
            session_id,
            normalize_slots(flat, existing={"_query_state": query_state} if query_state else None),
        )
    finally:
        db.close()


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


# ── slot corrections ─────────────────────────────────────────────────────


def test_explicit_score_correction_replaces_stale_value():
    first = slot_extract_node({"input_text": "河北物理类600分想学计算机", "slots": {}, "trace": []})
    assert first["slots"]["score_rank"] == "600分"

    second = slot_extract_node({"input_text": "我改成620分", "slots": first["slots"], "trace": []})
    assert second["slots"]["score_rank"] == "620分"
    assert second["slots"]["score"] == 620
    assert "score_rank" in second["trace"][-1]["overrides"]


def test_new_interest_replaces_old_negative_preference():
    first = slot_extract_node({"input_text": "河北物理类600分想学计算机", "slots": {}, "trace": []})
    second = slot_extract_node({"input_text": "我不想学计算机了，想学金融", "slots": first["slots"], "trace": []})
    assert second["slots"]["interest"] == "想学金融"


def test_passing_mention_does_not_overwrite_profile():
    first = slot_extract_node({"input_text": "河北物理类600分想学计算机", "slots": {}, "trace": []})
    second = slot_extract_node({"input_text": "北京校区怎么样", "slots": first["slots"], "trace": []})
    assert second["slots"]["province"] == "河北"
    assert second["slots"]["interest"] == "想学计算机"


# ── storage format round-trip ────────────────────────────────────────────


def test_storage_round_trip_produces_a_readable_profile(session_factory):
    sid = "format-round-trip"
    _seed(session_factory, sid, {**PROFILE, "score": 600})

    db = session_factory()
    try:
        stored = load_conversation_slots(db, sid)
    finally:
        db.close()
    assert stored["province"] == {"value": "河北", "filled": True}
    assert "score" not in stored

    restored = flatten_slots(stored)
    assert restored["province"] == "河北"
    assert restored["score"] == 600

    profile = load_profile(sid)
    assert profile.province == "河北"
    assert profile.score == 600
    assert profile.is_required_complete()


def test_legacy_flat_rows_are_readable():
    """Rows written by the old chat path must not blank the profile page."""
    from server.user_profile import UserProfile

    profile = UserProfile.from_slots({"province": "山东", "score": "631", "subject": "历史"})
    assert profile.province == "山东"
    assert profile.score == 631
    assert profile.subject == "历史"


# ── chat endpoint uses persisted state ───────────────────────────────────


def test_authenticated_chat_loads_persisted_profile(session_factory):
    sid = "chat-loads-profile"
    _seed(session_factory, sid, PROFILE)
    token = create_session_token(sid)

    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"session_id": sid, "scene": "gaokao", "message": "我这些信息你记住了吗"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    slots_events = [event for event in _events(response) if event.get("type") == "slots"]
    assert slots_events, "chat must emit the merged slots"
    assert slots_events[0]["data"]["province"] == "河北"
    assert slots_events[0]["data"]["interest"] == "想学计算机"

    profile = client.get(f"/api/v1/profile/{sid}", headers={"Authorization": f"Bearer {token}"}).json()
    assert profile["profile"]["province"] == "河北"
    assert profile["is_complete"] is True


def test_chat_persists_advanced_query_state(session_factory):
    sid = "chat-query-state"
    _seed(
        session_factory,
        sid,
        {"province": "河北"},
        {"round_count": 1, "asked_fields": ["province"], "skipped_fields": []},
    )
    token = create_session_token(sid)

    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"session_id": sid, "scene": "gaokao", "message": "你好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    db = session_factory()
    try:
        stored = load_conversation_slots(db, sid)
    finally:
        db.close()
    assert stored["_query_state"]["round_count"] == 2
    assert "score" in stored["_query_state"]["asked_fields"]


# ── structured card + offline template ──────────────────────────────────


def test_template_reply_lists_matched_schools_and_sources():
    state = {
        "scene": "gaokao",
        "slots": {"province": "河北", "score": 620, "subject": "物理", "interest": "计算机"},
        "data_query_results": {
            "match_schools": [
                {
                    "school_name": "北辰示例科技大学（合成）",
                    "school_level": "985",
                    "min_score": 600,
                    "data_source": "数据库位次法（合成）",
                }
            ],
            "rank_info": {"rank": 12345, "source": "本地合成数据"},
        },
        "structured_result": {"sources": ["数据库位次法（合成）"]},
        "reasoning": "",
        "trace": [],
    }
    reply = render_reply_node(state)["reply"]
    assert "推荐院校" in reply
    assert "北辰示例科技大学（合成）" in reply
    assert "数据库位次法（合成）" in reply


def test_template_reply_formats_major_lookup_as_user_facing_facts():
    state = {
        "scene": "gaokao",
        "slots": {"province": "示例省·甲", "score": 580, "subject": "物理", "interest": "计算机"},
        "data_query_results": {
            "major_info": {
                "name": "计算机科学与技术（合成）",
                "category": "工学",
                "sub_category": "计算机类",
                "employment_rate": 0.93,
                "avg_salary": 18500,
            }
        },
        "structured_result": {"sources": ["SYNTHETIC_DEMO 合成演示数据"]},
        "reasoning": "",
        "trace": [],
    }

    reply = render_reply_node(state)["reply"]

    assert "专业信息：计算机科学与技术（合成）" in reply
    assert "就业率93%" in reply
    assert "{'name':" not in reply
    assert "18500" not in reply


def test_structured_card_formats_rank_analysis_without_python_dict_repr():
    state = {
        "scene": "gaokao",
        "slots": {"province": "示例省·甲", "score": 580, "subject": "物理", "interest": "计算机"},
        "data_query_results": {
            "rank_info": {
                "rank": 57992,
                "confidence": "中-低（仅供参考）",
                "source": "本地数据库（合成数据）",
            }
        },
        "reasoning": "",
        "trace": [],
    }

    facts = structure_output_node(state)["structured_result"]["facts"]

    assert "位次参考：约57,992（置信度：中-低（仅供参考））" in facts
    assert all("{'rank':" not in fact for fact in facts)


def test_structured_card_carries_sources():
    state = {
        "scene": "gaokao",
        "slots": {"province": "河北", "score": 620, "subject": "物理", "interest": "计算机"},
        "data_query_results": {
            "match_schools": [
                {
                    "school_name": "北辰示例科技大学（合成）",
                    "school_level": "985",
                    "min_score": 600,
                    "data_source": "数据库位次法（合成）",
                }
            ]
        },
        "rag_sources": [{"source": "G1_core_method § 投档规则"}],
        "reasoning": "",
        "trace": [],
    }
    card = structure_output_node(state)["structured_result"]
    assert "数据库位次法（合成）" in card["sources"]
    assert "G1_core_method § 投档规则" in card["sources"]
