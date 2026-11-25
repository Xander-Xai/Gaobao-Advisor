"""Offline release audit: exact supported SQL inventory and original source bytes.

This validates an independently reviewed manifest, never generates approval.
No network, migration, import, or database writes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from db.models import AdmissionScore, School, YiFenYiDuan
from server.services.admissions_lineage import ManifestError, canonical_digest
from server.services.industrial_admissions import _COHORTS

_COHORT = {alias: group[0] for group in _COHORTS for alias in group}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_release(db, manifest: tuple[str, dict], sources_dir: Path) -> dict:
    """Compare every supported row, including rows outside a particular top-10 query."""
    pin, doc = manifest
    year = doc["year"]
    issues = {
        "source_missing_or_changed": 0,
        "unregistered_rows": 0,
        "changed_rows": 0,
        "missing_rows": 0,
        "ambiguous_rows": 0,
        "orphan_school_rows": 0,
        "synthetic_school_rows": 0,
    }
    counts = {"rank": 0, "school": 0, "rank_out_of_scope": 0, "school_out_of_scope": 0}
    for source in doc["sources"].values():
        expected = source["file_sha256"]
        try:
            valid = _file_digest(sources_dir / expected) == expected
        except OSError:
            valid = False
        if not valid:
            issues["source_missing_or_changed"] += 1

    seen = set()
    logical = set()

    def check(row_id: str, identity: tuple, values: dict) -> None:
        if identity in logical:
            issues["ambiguous_rows"] += 1
        logical.add(identity)
        seen.add(row_id)
        entry = doc["rows"].get(row_id)
        if entry is None:
            issues["unregistered_rows"] += 1
        elif canonical_digest(values) != entry["row_sha256"]:
            issues["changed_rows"] += 1

    ranks = db.query(YiFenYiDuan).filter(YiFenYiDuan.year == year).yield_per(1000)
    for row in ranks:
        cohort = _COHORT.get(row.subject_type)
        if not cohort or not row.province.strip() or not 100 <= row.score <= 750 or row.cumulative_count < 1:
            counts["rank_out_of_scope"] += 1
            continue
        counts["rank"] += 1
        check(
            f"yi_fen_yi_duan:{row.id}",
            ("rank", row.province, cohort, row.score),
            {
                "year": year,
                "province": row.province,
                "subject": row.subject_type,
                "score": row.score,
                "rank": row.cumulative_count,
            },
        )

    admissions = (
        db.query(AdmissionScore, School.name, School.special_type)
        .outerjoin(School, AdmissionScore.school_id == School.id)
        .filter(AdmissionScore.year == year, AdmissionScore.major_id.is_(None))
        .yield_per(1000)
    )
    for row, school, marker in admissions:
        if school is None:
            issues["orphan_school_rows"] += 1
            continue
        if marker == "SYNTHETIC_DEMO_DATA":
            issues["synthetic_school_rows"] += 1
            continue
        cohort = _COHORT.get(row.subject_type)
        if (
            not cohort
            or not row.province.strip()
            or row.batch is None
            or row.min_score is None
            or not 0 <= row.min_score <= 750
            or row.min_rank is None
            or row.min_rank < 1
        ):
            counts["school_out_of_scope"] += 1
            continue
        counts["school"] += 1
        check(
            f"admission_scores:{row.id}",
            ("school", row.province, cohort, row.school_id, row.batch),
            {
                "year": year,
                "province": row.province,
                "subject": row.subject_type,
                "id": row.id,
                "school": school,
                "school_id": row.school_id,
                "batch": row.batch,
                "min_score": row.min_score,
                "min_rank": row.min_rank,
            },
        )
    issues["missing_rows"] = len(set(doc["rows"]) - seen)
    return {
        "schema": "admissions-release-audit/v1",
        "dataset_version": doc["dataset_version"],
        "manifest_sha256": pin,
        "year": year,
        "counts": counts,
        "issues": issues,
        "passed": bool(counts["rank"] and counts["school"] and not any(issues.values())),
    }


def audit_mysql_snapshot(engine, manifest: tuple[str, dict], sources_dir: Path) -> dict:
    """Use one read-only consistent snapshot; DBA must provide an independent SELECT account."""
    if engine.dialect.name != "mysql":
        raise ManifestError("Release audit requires MySQL")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        try:
            connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            engines = connection.exec_driver_sql(
                "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
                "('schools', 'admission_scores', 'yi_fen_yi_duan')"
            ).all()
            if len(engines) != 3 or any(row[1] != "InnoDB" for row in engines):
                raise ManifestError("Release audit requires all three InnoDB tables")
            with Session(bind=connection, autoflush=False) as db:
                return audit_release(db, manifest, sources_dir)
        finally:
            connection.rollback()
