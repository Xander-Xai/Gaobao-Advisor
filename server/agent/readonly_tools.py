"""Allowlisted adapters to the *existing* Gaobao data and knowledge services.

Only SYNTHETIC_DEMO SQLite is accepted. No write or network tool is registered.
No public HTTP route uses these tools; invoke from an isolated evaluation process.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from server.agent.readonly_harness import ReadOnlyTool


def _source(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"synthetic:{kind}:{digest}"


def _require_demo_database() -> None:
    from db.database import DATABASE_URL, get_session
    from db.models import DatasetSnapshot

    if not DATABASE_URL.startswith("sqlite:///"):
        raise RuntimeError("Agent tool experiment requires isolated synthetic SQLite")
    if os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false":
        raise RuntimeError("Agent tool experiment requires outbound fetch disabled")
    db = get_session()
    try:
        snapshot = db.query(DatasetSnapshot).order_by(DatasetSnapshot.id.desc()).first()
        if snapshot is None or snapshot.profile != "SYNTHETIC_DEMO" or snapshot.is_synthetic != 1:
            raise RuntimeError("Missing verified SYNTHETIC_DEMO dataset snapshot")
    finally:
        db.close()


def _school(args: dict[str, Any]) -> dict[str, Any]:
    from server.services.data_query import query_school_info

    _require_demo_database()
    name = args["school_name"]
    if "（合成）" not in name:
        raise ValueError("Only synthetic school names are allowed")
    record = query_school_info(name)
    if not isinstance(record, dict):
        return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [], "record_count": 0}
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "source_ids": [_source("school", name)],
        "record_count": 1,
        "school_name": name,
        "province": str(record.get("province", ""))[:80],
    }


def _major(args: dict[str, Any]) -> dict[str, Any]:
    from server.services.data_query import query_major_info

    _require_demo_database()
    name = args["major_name"]
    if "（合成）" not in name:
        raise ValueError("Only synthetic major names are allowed")
    record = query_major_info(name)
    if not isinstance(record, dict):
        return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [], "record_count": 0}
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "source_ids": [_source("major", name)],
        "record_count": 1,
        "major_name": name,
        "category": str(record.get("category", ""))[:80],
    }


def _candidates(args: dict[str, Any]) -> dict[str, Any]:
    from server.services.data_query import query_match_schools_v2

    _require_demo_database()
    province, score, subject = args["province"], args["score"], args["subject_type"]
    if province not in {"示例省·甲", "示例省·乙", "示例省·丙", "示例省·丁"}:
        raise ValueError("Only fictional provinces are allowed")
    if not 100 <= score <= 750 or subject not in {"物理", "历史", "物理类", "历史类"}:
        raise ValueError("Unsupported synthetic score/subject")
    results = query_match_schools_v2(score=score, province=province, subject_type=subject, strategy="稳")
    if not isinstance(results, list):
        raise RuntimeError("Expected a list from candidate query")
    names = [str(item.get("school_name") or item.get("name") or "") for item in results[:5] if isinstance(item, dict)]
    names = [n for n in names if "（合成）" in n]
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "source_ids": [_source("candidate", n) for n in names],
        "record_count": len(names),
        "school_names": names,
    }


def _knowledge(args: dict[str, Any]) -> dict[str, Any]:
    from server.services import rag

    _require_demo_database()
    # Explicit corpus identity check: provider state alone does NOT prove origin.
    rag.ensure_default_initialized()
    if (
        rag.status()["state"] != "RAG_ENABLED"
        or rag.status()["provider"] != "keyword"
        or os.path.realpath(rag._groups_dir) != os.path.realpath(rag.DEFAULT_GROUPS_DIR)
    ):
        raise RuntimeError("Only default synthetic BM25 corpus is allowed")
    result = rag.search(args["query"], {})
    chunks = result.get("group_chunks", [])[:3]
    titles = [str(x.get("source") or x.get("section_title") or "") for x in chunks if isinstance(x, dict)]
    titles = [x for x in titles if x]
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "source_ids": [_source("knowledge", title) for title in titles],
        "record_count": len(titles),
        "titles": titles,
    }


def synthetic_readonly_tools() -> dict[str, ReadOnlyTool]:
    """Never register arbitrary Python, shell, SQL execution or external API tools."""
    return {
        "school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, _school),
        "major_lookup": ReadOnlyTool("major_lookup", {"major_name": str}, _major),
        "candidate_lookup": ReadOnlyTool(
            "candidate_lookup", {"province": str, "score": int, "subject_type": str}, _candidates
        ),
        "knowledge_lookup": ReadOnlyTool("knowledge_lookup", {"query": str}, _knowledge),
    }
