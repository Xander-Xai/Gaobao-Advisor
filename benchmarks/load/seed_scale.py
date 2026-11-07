"""Add deterministic, explicitly synthetic MySQL rows for LOAD BENCHMARKS only.

Refuses to alter databases with unknown, custom, or mixed provenance.
Never use this command on a live admissions database.
"""

from __future__ import annotations

import argparse

from db.database import IS_SQLITE, SessionLocal
from db.models import AdmissionScore, EnrollmentPlan, Major, School
from server.services.data_profile import detect_data_profile

NAME_PREFIX = "性能压测虚构院校"


def seed_synthetic_load(schools: int) -> dict:
    if IS_SQLITE:
        raise RuntimeError("Load amplification requires the isolated MySQL Java Compose profile")
    with SessionLocal() as db:
        profile = detect_data_profile(db)
        if profile != "SYNTHETIC_DEMO":
            raise RuntimeError(f"Refusing non-synthetic profile: {profile}")
        major_ids = [row[0] for row in db.query(Major.id).order_by(Major.id).limit(2).all()]
        if len(major_ids) < 2:
            raise RuntimeError("Expected at least two synthetic demo majors")

        existing = {row[0] for row in db.query(School.name).filter(School.name.like(f"{NAME_PREFIX}%")).all()}
        new_schools = []
        for index in range(1, schools + 1):
            name = f"{NAME_PREFIX}{index:05d}（合成）"
            if name in existing:
                continue
            new_schools.append(
                School(
                    name=name,
                    province="示例省·甲",
                    city=f"模拟城市{index % 10}",
                    level="普通",
                    school_type="理工",
                    special_type="SYNTHETIC_DEMO_DATA",
                    data_source_note="SYNTHETIC_BENCHMARK_ONLY; not actual admissions data",
                )
            )
        db.add_all(new_schools)
        db.flush()
        score_rows = []
        plan_rows = []
        for school in new_schools:
            for year in (2023, 2024, 2025):
                for province in ("示例省·甲", "示例省·乙"):
                    for major_id in major_ids:
                        plan_rows.append(
                            {
                                "school_id": school.id,
                                "major_id": major_id,
                                "province": province,
                                "year": year,
                                "plan_count": 10 + school.id % 20,
                                "batch": "本科批",
                            }
                        )
                        for subject in ("物理类", "历史类"):
                            score_rows.append(
                                {
                                    "school_id": school.id,
                                    "major_id": major_id,
                                    "province": province,
                                    "year": year,
                                    "batch": "本科批",
                                    "subject_type": subject,
                                    "min_score": 480 + school.id % 100 + (year - 2023) * 2,
                                    "min_rank": 10000 + school.id % 15000,
                                    "plan_count": 10 + school.id % 20,
                                }
                            )
        if score_rows:
            db.bulk_insert_mappings(AdmissionScore, score_rows)
            db.bulk_insert_mappings(EnrollmentPlan, plan_rows)
        db.commit()
        return {
            "new_schools": len(new_schools),
            "new_admission_scores": len(score_rows),
            "new_enrollment_plans": len(plan_rows),
            "synthetic_only": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schools", type=int, default=500)
    parser.add_argument("--confirm-synthetic-only", action="store_true")
    options = parser.parse_args()
    if not options.confirm_synthetic_only:
        parser.error("--confirm-synthetic-only required")
    if not 1 <= options.schools <= 3000:
        parser.error("schools must be 1..3000")
    result = seed_synthetic_load(options.schools)
    print(f"Load-scale synthetic seed: {result}", flush=True)


if __name__ == "__main__":
    main()
