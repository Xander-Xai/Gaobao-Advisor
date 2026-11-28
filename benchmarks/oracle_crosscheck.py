"""Cross-check the application query layer against independent SQL answers.

This offline tool accepts a private local SQLite snapshot. It copies the
snapshot before the application ORM opens it, keeps the oracle connection in
SQLite read-only mode, and emits aggregate pass/fail counts only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def _rank_candidates(subject: str) -> list[str]:
    aliases = {
        "物理": ["物理", "物理类", "理科", "3+3综合", "综合"],
        "物理类": ["物理类", "理科", "物理", "3+3综合", "综合"],
        "历史": ["历史", "历史类", "文科", "3+3综合", "综合"],
        "历史类": ["历史类", "文科", "历史", "3+3综合", "综合"],
        "文科": ["文科"],
        "理科": ["理科"],
        "3+3综合": ["3+3综合"],
    }
    return aliases.get(subject, [subject])


def _oracle_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def crosscheck(db_path: Path, sample_limit: int = 60) -> dict[str, Any]:
    oracle = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    oracle.row_factory = sqlite3.Row
    temp_dir = Path(tempfile.mkdtemp(prefix="gaobao-oracle-crosscheck-"))
    app_db = temp_dir / "app.db"
    shutil.copy2(db_path, app_db)
    os.environ["DATABASE_URL"] = f"sqlite:///{app_db}"
    os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
    try:
        from legacy import gaokao_data

        checks = 0
        mismatches = 0
        categories: dict[str, dict[str, int]] = {}

        def record(category: str, passed: bool) -> None:
            nonlocal checks, mismatches
            checks += 1
            counts = categories.setdefault(category, {"checks": 0, "passed": 0, "failed": 0})
            counts["checks"] += 1
            counts["passed" if passed else "failed"] += 1
            if not passed:
                mismatches += 1

        schools = [
            r["name"] for r in _oracle_rows(oracle, "SELECT name FROM schools ORDER BY id LIMIT ?", (sample_limit,))
        ]
        for school in schools:
            app = gaokao_data.query_admission(school, province="河南", year=2024)
            expected = _oracle_rows(
                oracle,
                "SELECT a.min_score FROM admission_scores a JOIN schools s ON s.id=a.school_id"
                " WHERE s.name=? AND a.province=? AND a.year=? ORDER BY a.min_score",
                (school, "河南", 2024),
            )
            got = sorted(r["min_score"] for r in app if r.get("min_score") is not None)
            want = sorted(r["min_score"] for r in expected if r["min_score"] is not None)
            record("admission_min_scores", got == want)

            app_plans = gaokao_data.query_enrollment_plan(school, province="河南", year=2024)
            expected_plans = _oracle_rows(
                oracle,
                "SELECT e.plan_count FROM enrollment_plans e JOIN schools s ON s.id=e.school_id"
                " WHERE s.name=? AND e.province=? AND e.year=?",
                (school, "河南", 2024),
            )
            got_total = sum(r.get("plan_count") or 0 for r in app_plans)
            want_total = sum(r["plan_count"] or 0 for r in expected_plans)
            record("enrollment_plan_totals", got_total == want_total)

        rank_cases = (
            ("河北", 2025, "物理类"),
            ("河南", 2024, "物理类"),
            ("广东", 2023, "历史类"),
            ("江苏", 2024, "物理类"),
            ("山东", 2025, "3+3综合"),
        )
        for province, year, subject in rank_cases:
            for score in (520, 600, 650):
                got = gaokao_data.query_yi_fen_yi_duan(province, score, subject_type=subject, year=year)
                expected = None
                for track in _rank_candidates(subject):
                    rows = _oracle_rows(
                        oracle,
                        "SELECT score,cumulative_count,subject_type FROM yi_fen_yi_duan"
                        " WHERE province=? AND year=? AND subject_type=? AND score<=?"
                        " ORDER BY score DESC LIMIT 1",
                        (province, year, track, score),
                    )
                    if rows:
                        expected = rows[0]["cumulative_count"]
                        break
                record("track_aware_rank", isinstance(got, dict) and got.get("rank") == expected)

        for province, subject, score in (("河北", "物理类", 600), ("河南", "物理类", 550)):
            app = gaokao_data.query_match_schools_v2(score, province=province, subject_type=subject, year=2025)
            expected = {
                r["name"]
                for r in _oracle_rows(
                    oracle,
                    "SELECT DISTINCT s.name FROM admission_scores a JOIN schools s ON s.id=a.school_id"
                    " WHERE a.province=? AND a.subject_type=? AND a.year=2025 AND a.min_score IS NOT NULL",
                    (province, subject),
                )
            }
            got = {r.get("school_name") or r.get("name") for r in app if isinstance(r, dict)}
            record("match_school_source_bound", got <= expected)

        ambiguous = _oracle_rows(
            oracle,
            "SELECT COUNT(*) AS n FROM (SELECT province,year,score FROM yi_fen_yi_duan"
            " GROUP BY province,year,score HAVING COUNT(DISTINCT subject_type)>1)",
        )[0]["n"]
        total_keys = _oracle_rows(
            oracle,
            "SELECT COUNT(*) AS n FROM (SELECT province,year,score FROM yi_fen_yi_duan GROUP BY 1,2,3)",
        )[0]["n"]
        return {
            "schema": "gaobao-oracle-crosscheck/v1",
            "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
            "checks": checks,
            "mismatches": mismatches,
            "categories": categories,
            "ambiguous_track_band_keys": ambiguous,
            "total_band_keys": total_keys,
            "ambiguous_track_ratio": round(ambiguous / total_keys, 4) if total_keys else None,
            "status": "PASS" if mismatches == 0 else "FAIL",
        }
    finally:
        oracle.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=60)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output file already exists")
    report = crosscheck(args.db, args.sample_limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": report["checks"], "mismatches": report["mismatches"], "status": report["status"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
