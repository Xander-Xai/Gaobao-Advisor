"""Synthetic SQL regressions for exact cohort, year and evidence rendering."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.database import Base
from db.models import AdmissionScore, School, YiFenYiDuan
from server.services.industrial_admissions import build_history_reply, exact_score, query_history


@pytest.fixture
def history_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                School(id=1, name="fixture correct school", province="广东"),
                School(id=2, name="fixture incorrect school", province="广东"),
            ]
        )
        db.add_all(
            [
                YiFenYiDuan(
                    id=1,
                    province="广东",
                    year=2025,
                    subject_type="物理类",
                    score=600,
                    cumulative_count=1000,
                ),
                YiFenYiDuan(
                    id=2,
                    province="广东",
                    year=2025,
                    subject_type="历史类",
                    score=600,
                    cumulative_count=2000,
                ),
                YiFenYiDuan(
                    id=3,
                    province="广东",
                    year=2024,
                    subject_type="物理类",
                    score=600,
                    cumulative_count=3000,
                ),
            ]
        )
        db.add_all(
            [
                AdmissionScore(
                    id=1,
                    school_id=1,
                    province="广东",
                    year=2025,
                    subject_type="物理类",
                    min_score=599,
                    min_rank=1020,
                ),
                AdmissionScore(
                    id=2,
                    school_id=2,
                    province="广东",
                    year=2025,
                    subject_type="历史类",
                    min_score=600,
                    min_rank=1000,
                ),
                AdmissionScore(
                    id=3,
                    school_id=2,
                    province="广东",
                    year=2024,
                    subject_type="物理类",
                    min_score=600,
                    min_rank=1000,
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_history_never_crosses_year_or_cohort(history_db):
    evidence = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    assert evidence["rank"] == 1000
    assert evidence["status"] == "HISTORICAL_ROWS"
    assert [row["id"] for row in evidence["rows"]] == [1]
    assert (
        query_history(history_db, province="广东", score=601, subject="物理", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )
    assert (
        query_history(history_db, province="广东", score=600, subject="综合", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )


def test_ambiguous_alias_rows_refuse(history_db):
    history_db.add(YiFenYiDuan(province="广东", year=2025, subject_type="物理", score=600, cumulative_count=1100))
    history_db.commit()
    assert (
        query_history(history_db, province="广东", score=600, subject="物理", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )


def test_industrial_text_and_card_share_exact_receipts(history_db, monkeypatch):
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    monkeypatch.setenv("GAOBAO_DATA_APPROVAL_REF", "fixture-approval")
    evidence = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    reply, card = build_history_reply({"scene": "gaokao", "data_query_results": {"industrial_history": evidence}})
    assert "fixture correct school" in reply
    assert "fixture incorrect school" not in reply
    assert "admission_scores:1" in card["sources"]
    assert not card["suggestions"]
    assert card["confidence"] == 0
    refused, card = build_history_reply({"scene": "gaokao", "knowledge_query": True})
    assert "尚未获准发布" in refused
    assert not card["facts"]


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({"score": 600}, 600),
        ({"score_rank": "600分 / 位次3000"}, 600),
        ({"score": "600", "score_rank": "600分"}, 600),
        ({"score": 600, "score_rank": "601分"}, None),
        ({"score_rank": "位次600"}, None),
        ({"score_rank": "600"}, None),
        ({"score_rank": "6000分"}, None),
        ({"score_rank": "一本线上100分"}, None),
        ({"score": 600.5}, None),
        ({"score": True}, None),
    ],
)
def test_industrial_score_requires_exact_unambiguous_value(slots, expected):
    assert exact_score(slots) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("广东600分物理类", 600),
        ("我改成620分", 620),
        ("广东1600分物理类", None),
        ("广东600.5分物理类", None),
        ("广东-600分物理类", None),
        ("广东600分左右", None),
        ("一本线上100分", None),
        ("不到600分", None),
        ("600分还是610分", None),
        ("考了600", None),
    ],
)
def test_industrial_current_turn_score_precedes_legacy_extraction(monkeypatch, message, expected):
    from server.graph.nodes.extract import slot_extract_node

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    state = {"input_text": message, "slots": {"province": "广东", "subject": "物理类", "score_rank": "590分"}}
    result = slot_extract_node(state)
    assert result["industrial_score_invalid"] is (expected is None)
    assert exact_score(result["slots"]) == expected


def test_industrial_invalid_score_never_queries_sql(monkeypatch):
    from unittest.mock import Mock

    from server.graph.nodes.data_nodes import data_query_node
    from server.graph.nodes.extract import slot_extract_node

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    session = Mock(side_effect=AssertionError("invalid input reached SQL"))
    monkeypatch.setattr("db.database.get_session", session)
    state = {
        "input_text": "广东1600分物理类",
        "scene": "gaokao",
        "slots": {"province": "广东", "subject": "物理类", "score_rank": "600分"},
    }
    state.update(slot_extract_node(state))
    state.update(data_query_node(state))
    reply, card = build_history_reply(state)
    session.assert_not_called()
    assert "明确的高考整数总分" in reply
    assert not card["facts"]
