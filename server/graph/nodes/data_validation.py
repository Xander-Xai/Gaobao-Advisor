"""Post-query provenance validation.

Compare only independent records describing the SAME school and admissions
cohort. Scores from different schools cannot be cross-validation samples.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from server.services.quality import QualityOrchestrator


def validate_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Mark single-source evidence explicitly; never claim synthetic SQL is verified."""
    data = state.get("data_query_results") or {}
    rows = data.get("match_schools") or []
    trace = list(state.get("trace") or [])
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        school = str(row.get("school_name") or row.get("name") or "").strip()
        if not school or row.get("min_score") is None:
            continue
        # Cohort identity matters: otherwise two years/provinces could appear
        # to corroborate one another despite describing different admissions.
        key = (
            school,
            str(row.get("major_name") or ""),
            str(row.get("province") or state.get("slots", {}).get("province") or ""),
            str(row.get("year") or ""),
            str(row.get("subject_type") or state.get("slots", {}).get("subject") or ""),
        )
        grouped[key].append(row)

    checker = QualityOrchestrator()
    checks: list[dict[str, Any]] = []
    single_source_count = 0
    for key, group in grouped.items():
        # An SQL join may duplicate the same record. Count distinct named
        # sources, not rows, to avoid manufactured multi-source confidence.
        sources = {}
        for row in group:
            source = str(row.get("data_source") or row.get("source") or "unverified_synthetic_sql")
            sources.setdefault(source, {
                "source": source,
                "min_score": row.get("min_score"),
                "min_rank": row.get("min_rank"),
            })
        report = checker.cross_validate(list(sources.values()))
        if not report:
            continue
        single = len(sources) < 2
        single_source_count += int(single)
        checks.append({
            "school": key[0],
            "confidence": "低" if single else report.get("confidence", "低"),
            "source_count": len(sources),
            "note": "仅单一数据来源，未经独立交叉验证" if single else report.get("note", ""),
        })

    trace.append({
        "node": "validate_data",
        "event": "provenance_checked",
        "school_count": len(checks),
        "single_source_count": single_source_count,
    })
    return {
        "validation": {
            "school_checks": checks,
            "single_source_count": single_source_count,
            "status": "SINGLE_SOURCE" if single_source_count else ("CHECKED" if checks else "NO_DATA"),
        },
        "trace": trace,
    }
