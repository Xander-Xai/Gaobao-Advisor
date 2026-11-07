"""Public data explorer endpoints: coverage, browsing, detail and trends."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import (
    AdmissionScore,
    DatasetSnapshot,
    EnrollmentPlan,
    Major,
    School,
    SubjectRanking,
)
from scripts.seed_demo_data import DEMO_YEARS, build_payload
from server.deps import get_db
from server.main import app


def _seed_clean(session) -> None:
    """Seed only the deterministic payload, without the admin anomaly rows."""
    payload = build_payload()
    schools = [School(**row) for row in payload["schools"]]
    majors = [Major(**row) for row in payload["majors"]]
    session.add_all(schools)
    session.add_all(majors)
    session.flush()
    session.add_all(
        [
            AdmissionScore(
                school_id=schools[row["school_idx"]].id,
                major_id=majors[row["major_idx"]].id,
                province=row["province"],
                year=row["year"],
                batch=row["batch"],
                standardized_batch=row["standardized_batch"],
                subject_type=row["subject_type"],
                min_score=row["min_score"],
                avg_score=row["avg_score"],
                max_score=row["max_score"],
                min_rank=row["min_rank"],
                plan_count=row["plan_count"],
            )
            for row in payload["admission_scores"]
        ]
    )
    session.add_all(
        [
            EnrollmentPlan(
                school_id=schools[row["school_idx"]].id,
                major_id=majors[row["major_idx"]].id,
                province=row["province"],
                year=row["year"],
                plan_count=row["plan_count"],
                subject_requirement=row["subject_requirement"],
                batch=row["batch"],
                duration=row["duration"],
                tuition=row["tuition"],
            )
            for row in payload["enrollment_plans"]
        ]
    )
    session.add_all(
        [
            SubjectRanking(
                school_id=schools[row["school_idx"]].id,
                major_category=row["major_category"],
                ranking_source=row["ranking_source"],
                ranking_year=row["ranking_year"],
                ranking_position=row["ranking_position"],
                grade=row["grade"],
            )
            for row in payload["subject_rankings"]
        ]
    )
    session.add(
        DatasetSnapshot(
            id=1,
            dataset_version=payload["metadata"]["dataset_version"],
            profile="SYNTHETIC_DEMO",
            source_note=payload["metadata"]["notice"],
            generated_at=datetime.datetime(2026, 1, 1, 0, 0, 0),
            year_from=min(DEMO_YEARS),
            year_to=max(DEMO_YEARS),
            seed=payload["metadata"]["seed"],
            is_synthetic=1,
        )
    )
    session.commit()


@pytest.fixture
def data_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'data-catalog.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = factory()
    try:
        _seed_clean(session)
    finally:
        session.close()
    yield factory
    engine.dispose()


@pytest.fixture
def data_client(data_factory):
    def override_get_db():
        session = data_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()


class TestOverview:
    def test_counts_and_coverage_are_computed_from_rows(self, data_client):
        response = data_client.get("/api/v1/data/overview")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data_profile"] == "SYNTHETIC_DEMO"
        assert body["counts"]["schools"] == 8
        assert body["counts"]["majors"] == 12
        assert body["counts"]["admission_scores"] == len(build_payload()["admission_scores"])
        assert body["years"] == list(DEMO_YEARS)
        assert body["year_from"] == DEMO_YEARS[0]
        assert body["year_to"] == DEMO_YEARS[-1]
        assert sum(item["count"] for item in body["scores_by_year"]) == body["counts"]["admission_scores"]
        assert body["schools_with_scores"] > 0
        assert body["snapshot"]["profile"] == "SYNTHETIC_DEMO"
        assert body["snapshot"]["is_synthetic"] is True
        assert "SYNTHETIC_DEMO" in body["notice"]

    def test_overview_batched_counts_match_grouped_years(self, data_client):
        """Issue #91 regression: batched scalar-subquery COUNTs must stay
        consistent with the per-year GROUP BY query and distinct-school
        aggregates on the same fixture.
        """
        response = data_client.get("/api/v1/data/overview")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["years"] == [item["year"] for item in body["scores_by_year"]]
        assert body["counts"]["admission_scores"] == sum(item["count"] for item in body["scores_by_year"])
        assert body["schools_with_scores"] >= body["schools_with_plans"] - body["counts"]["schools"]
        assert body["counts"]["schools"] == 8

    def test_filter_options_come_from_database(self, data_client):
        response = data_client.get("/api/v1/data/filters")
        assert response.status_code == 200
        body = response.json()
        assert "示例省·甲" in body["provinces"]
        assert "985" in body["levels"]
        assert body["years"] == sorted(DEMO_YEARS, reverse=True)
        assert set(body["subject_types"]) == {"物理类", "历史类"}
        assert body["majors"]


class TestSchoolBrowser:
    def test_cursor_pagination_and_total(self, data_client):
        first = data_client.get("/api/v1/data/schools", params={"limit": 3}).json()
        assert first["total"] == 8
        assert len(first["items"]) == 3
        assert first["has_more"] is True
        second = data_client.get(
            "/api/v1/data/schools",
            params={"limit": 3, "cursor": first["next_cursor"]},
        ).json()
        assert len(second["items"]) == 3
        assert {item["id"] for item in first["items"]}.isdisjoint({item["id"] for item in second["items"]})

    def test_keyword_and_filters(self, data_client):
        keyword = data_client.get("/api/v1/data/schools", params={"q": "北辰"}).json()
        assert keyword["total"] == 1
        assert "北辰" in keyword["items"][0]["name"]
        assert keyword["items"][0]["score_count"] > 0

        province = data_client.get("/api/v1/data/schools", params={"province": "示例省·甲"}).json()
        assert province["items"]
        assert all(item["province"] == "示例省·甲" for item in province["items"])

        level = data_client.get("/api/v1/data/schools", params={"level": "985"}).json()
        assert level["items"]
        assert all(item["level"] == "985" for item in level["items"])

    def test_school_detail_has_scores_plans_and_availability(self, data_client):
        listing = data_client.get("/api/v1/data/schools", params={"limit": 1}).json()
        school_id = listing["items"][0]["id"]
        response = data_client.get(f"/api/v1/data/schools/{school_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == school_id
        assert body["scores"]
        assert body["plans"]
        assert body["data_availability"]["has_scores"] is True
        assert body["data_availability"]["available_years"] == list(DEMO_YEARS)
        assert set(body["data_availability"]["subject_types"]) <= {"物理类", "历史类"}

    def test_missing_school_is_404(self, data_client):
        assert data_client.get("/api/v1/data/schools/999999").status_code == 404


class TestTrends:
    def test_trend_keeps_subject_types_separate(self, data_client):
        response = data_client.get(
            "/api/v1/data/trends",
            params={
                "school_name": "北辰示例科技大学（合成）",
                "province": "示例省·甲",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == "school"
        assert len(body["series"]) == 2
        subjects = [item["subject_type"] for item in body["series"]]
        assert sorted(subjects) == ["历史类", "物理类"]
        for series in body["series"]:
            years = [point["year"] for point in series["points"]]
            assert years == list(DEMO_YEARS)
            assert series["direction"] in {"逐年上升", "逐年下降", "波动", "数据不足"}
        assert body["missing_years"] == []

    def test_subject_filter_returns_one_series(self, data_client):
        response = data_client.get(
            "/api/v1/data/trends",
            params={
                "school_name": "北辰示例科技大学（合成）",
                "province": "示例省·甲",
                "subject_type": "物理类",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert [series["subject_type"] for series in body["series"]] == ["物理类"]

    def test_unknown_school_is_404(self, data_client):
        response = data_client.get(
            "/api/v1/data/trends",
            params={"school_name": "不存在的院校", "province": "示例省·甲"},
        )
        assert response.status_code == 404
