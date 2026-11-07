"""Reproducible overview performance benchmark (Issue #91).

Builds a fixed synthetic fixture (500 schools / 3,000 admission rows /
1,000 plan rows, seed 42) in a temporary SQLite database and times
``server.services.data_catalog.get_overview`` under concurrent threads.

Usage:
    python benchmarks/overview_perf_bench.py [--seconds 30] [--rounds 3]

Zero completed requests are reported as undefined (median/p95 = null),
matching the CI evidence convention from Issue #90.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime

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
    YiFenYiDuan,
)

FIXTURE_SEED = 42
SCHOOLS = 500
PROVINCES = ["示例省·甲", "示例省·乙"]


def build_fixture() -> sessionmaker:
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/bench.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory: sessionmaker = sessionmaker(bind=engine)
    s = session_factory()
    major = Major(name="计算机科学与技术（合成）", category="计算机")
    s.add(major)
    s.flush()
    random.seed(FIXTURE_SEED)
    seen: set[tuple[int, str, int, str]] = set()
    for i in range(SCHOOLS):
        school = School(
            name=f"虚构院校{i}（合成）",
            province=random.choice(PROVINCES),
            city="示例市·晨",
            level="本科",
            school_type="综合",
            special_type="SYNTHETIC_DEMO_DATA",
            data_source_note="synthetic",
        )
        s.add(school)
        s.flush()
        for j in range(24):
            prov = PROVINCES[j % 2]
            year = 2024 + j % 3
            subject = ["物理", "历史"][j % 2]
            if (school.id, prov, year, subject) in seen:
                continue
            seen.add((school.id, prov, year, subject))
            s.add(
                AdmissionScore(
                    school_id=school.id,
                    major_id=major.id,
                    province=prov,
                    year=year,
                    batch="本科",
                    subject_type=subject,
                    min_score=400 + random.randint(0, 300),
                    avg_score=500,
                    max_score=700,
                    min_rank=1000,
                    plan_count=10,
                )
            )
        for j in range(2):
            s.add(
                EnrollmentPlan(
                    school_id=school.id,
                    major_id=major.id,
                    province=PROVINCES[j],
                    year=2024 + j,
                    plan_count=50,
                    subject_requirement="物理",
                    batch="本科",
                    duration="4",
                    tuition="5000",
                )
            )
        s.add(SubjectRanking(school_id=school.id, major_category="计算机", ranking_source="syn", ranking_year=2024))
    s.add(YiFenYiDuan(province="示例省·甲", year=2024, score=500, cumulative_count=1000))
    s.add(
        DatasetSnapshot(
            dataset_version="1",
            profile="SYNTHETIC_DEMO",
            source_note="syn",
            generated_at=datetime.now(),
            year_from=2024,
            year_to=2026,
            seed=20240617,
            is_synthetic=True,
        )
    )
    s.commit()
    s.close()
    return session_factory


def run_users(session_factory: sessionmaker, users: int, seconds: int) -> dict:
    from server.services.data_catalog import get_overview

    times: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    deadline = time.time() + seconds

    def worker() -> None:
        while time.time() < deadline:
            start = time.perf_counter()
            try:
                session = session_factory()
                get_overview(session)
                session.close()
                with lock:
                    times.append((time.perf_counter() - start) * 1000)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(str(exc)[:80])

    threads = [threading.Thread(target=worker) for _ in range(users)]
    begin = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    duration = time.time() - begin
    result: dict = {
        "users": users,
        "seconds": seconds,
        "completed": len(times),
        "errors": len(errors),
    }
    if times:
        ordered = sorted(times)
        result.update(
            {
                "median_ms": round(statistics.median(times), 2),
                "p95_ms": round(ordered[int(0.95 * len(ordered))], 2),
                "rps": round(len(times) / duration, 2),
            }
        )
    else:
        result.update({"median_ms": None, "p95_ms": None, "rps": None})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds", type=int, default=30, help="measurement window per tier (must exceed client timeout)"
    )
    parser.add_argument("--rounds", type=int, default=3, help="repeat each tier this many times; report range")
    parser.add_argument("--tiers", default="1,5,10,20,50", help="comma-separated concurrent users")
    args = parser.parse_args()
    session_factory = build_fixture()
    print(json.dumps({"fixture_seed": FIXTURE_SEED, "schools": SCHOOLS, "python": sys.version.split()[0]}, indent=2))
    for tier in [int(t) for t in args.tiers.split(",")]:
        rounds = [run_users(session_factory, tier, args.seconds) for _ in range(args.rounds)]
        for entry in rounds:
            print(json.dumps(entry))
        medians = [r["median_ms"] for r in rounds if r["median_ms"] is not None]
        if medians:
            print(json.dumps({"users": tier, "median_range_ms": [round(min(medians), 2), round(max(medians), 2)]}))


if __name__ == "__main__":
    main()
