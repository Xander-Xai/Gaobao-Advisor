"""Track-aware regression tests for the one-score-one-rank lookup."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import AdmissionScore, Base, YiFenYiDuan
from legacy import gaokao_data


@pytest.fixture
def lookup_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'bands.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                YiFenYiDuan(province="测试省", year=2025, subject_type="物理类", score=600, cumulative_count=27073),
                YiFenYiDuan(province="测试省", year=2025, subject_type="历史类", score=600, cumulative_count=6004),
                YiFenYiDuan(province="测试省", year=2025, subject_type="物理类", score=590, cumulative_count=30000),
                YiFenYiDuan(province="旧省", year=2024, subject_type="理科", score=600, cumulative_count=2200),
                YiFenYiDuan(province="旧省", year=2024, subject_type="文科", score=600, cumulative_count=400),
                YiFenYiDuan(province="旧省", year=2024, subject_type="综合", score=600, cumulative_count=3784),
                AdmissionScore(
                    school_id=1,
                    major_id=None,
                    province="反推省",
                    year=2024,
                    batch="本科批",
                    subject_type="理科",
                    min_score=600,
                    min_rank=2200,
                ),
                AdmissionScore(
                    school_id=2,
                    major_id=None,
                    province="反推省",
                    year=2024,
                    batch="本科批",
                    subject_type="综合",
                    min_score=600,
                    min_rank=400,
                ),
            ]
        )
        session.commit()

    def factory():
        return Session(engine)

    monkeypatch.setattr(gaokao_data, "_get_session", factory)
    yield gaokao_data.query_yi_fen_yi_duan
    engine.dispose()


def test_exact_track_wins_over_another_track(lookup_session):
    result = lookup_session("测试省", 600, subject_type="物理类", year=2025)
    assert result["rank"] == 27073
    assert result["subject_type"] == "物理类"
    assert result["subject_track_matched"] is True
    assert result["confidence"] == "高"


def test_history_rank_is_not_returned_for_physics_track(lookup_session):
    result = lookup_session("测试省", 600, subject_type="历史类", year=2025)
    assert result["rank"] == 6004
    assert result["subject_type"] == "历史类"


def test_legacy_track_is_used_before_broader_composite_track(lookup_session):
    result = lookup_session("旧省", 600, subject_type="物理类", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"
    assert result["requested_subject_type"] == "物理类"
    assert result["subject_track_matched"] is False
    assert result["confidence"] == "中"


def test_short_physics_alias_still_prefers_legacy_science_track(lookup_session):
    result = lookup_session("旧省", 600, subject_type="物理", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"


def test_score_below_available_track_range_is_refused(lookup_session):
    result = lookup_session("测试省", 100, subject_type="物理类", year=2025)
    assert result["rank"] is None
    assert result["range_min_score"] == 590
    assert result["confidence"] == "无"
    assert "低于" in result["message"]


def test_admission_score_fallback_uses_one_track_at_a_time(lookup_session):
    result = lookup_session("反推省", 600, subject_type="物理类", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"


def test_admission_score_fallback_refuses_score_below_available_range(lookup_session):
    result = lookup_session("反推省", 100, subject_type="物理类", year=2024)
    assert result["rank"] is None
    assert result["range_min_score"] == 600
    assert result["confidence"] == "无"
