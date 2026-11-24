"""Isolated, no-provider SQL/RAG serial-vs-parallel probe on SYNTHETIC_DEMO.

This is a paired *local read-only microbenchmark*. It never runs on the
configured application database. Parallel execution is experimental and is
not wired into the production graph. It cannot prove production speedups.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        raise ValueError("empty measurement")
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo), 3)


def _signature(sql: dict[str, Any], rag: dict[str, Any]) -> str:
    return json.dumps(
        {
            "sql": sql.get("data_query_results"),
            "rag_state": rag.get("rag_state"),
            "rag_evidence": rag.get("rag_evidence"),
            "rag_sources": rag.get("rag_sources"),
            "rag_chunks": rag.get("rag_chunks"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _probe(*, repeat: int, warmup: int) -> dict:
    if os.getenv("APP_ENV", "").strip().lower() in {"production", "prod"}:
        raise RuntimeError("SQL/RAG microbenchmark cannot run in production mode")
    # db.database creates its engine at import time; refuse to reuse an
    # already-loaded application engine, even when it points to SQLite.
    if "db.database" in sys.modules:
        raise RuntimeError("run the probe in a fresh Python process")

    with tempfile.TemporaryDirectory(prefix="gaobao_sql_rag_benchmark_") as tmpdir:
        db_path = Path(tmpdir) / "synthetic_only.sqlite"
        # Only this private, newly created temporary SQLite may be opened.
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        from scripts.seed_demo_data import DEMO_MAJORS, seed_database

        seed_database(db_path, reset=False)
        from server.graph.nodes.data_nodes import data_query_node
        from server.graph.nodes.rag_node import rag_retrieve_node
        from server.services import rag as rag_service

        if not rag_service.initialize_default() or rag_service.status().get("state") != "RAG_ENABLED":
            raise RuntimeError("bundled synthetic RAG corpus unavailable")

        state = {
            "input_text": "平行志愿规则如何理解，并结合分数介绍专业？",
            "scene": "gaokao",
            "slots": {
                "province": "示例省·甲",
                "subject": "物理类",
                "score": 550,
                "interest": DEMO_MAJORS[0][0],
            },
        }

        def run_serial() -> tuple[float, str, bool]:
            started = time.perf_counter()
            sql = data_query_node(dict(state))
            rag = rag_retrieve_node(dict(state))
            duration = (time.perf_counter() - started) * 1000
            return duration, _signature(sql, rag), "error" not in (sql.get("data_query_results") or {})

        def run_parallel(pool: concurrent.futures.ThreadPoolExecutor) -> tuple[float, str, bool]:
            started = time.perf_counter()
            sql_future = pool.submit(data_query_node, dict(state))
            rag_future = pool.submit(rag_retrieve_node, dict(state))
            sql = sql_future.result()
            rag = rag_future.result()
            duration = (time.perf_counter() - started) * 1000
            return duration, _signature(sql, rag), "error" not in (sql.get("data_query_results") or {})

        serial_ms: list[float] = []
        parallel_ms: list[float] = []
        equivalent = True
        sql_ok = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            # Warm both execution modes, not just the serial baseline.
            for _ in range(warmup):
                run_serial()
                run_parallel(pool)
            for i in range(repeat):
                # Alternate measurement order to reduce warm-cache bias.
                first, second = (run_serial(), run_parallel(pool)) if i % 2 == 0 else (run_parallel(pool), run_serial())
                serial, parallel = (first, second) if i % 2 == 0 else (second, first)
                serial_ms.append(round(serial[0], 3))
                parallel_ms.append(round(parallel[0], 3))
                equivalent = equivalent and serial[1] == parallel[1]
                sql_ok = sql_ok and serial[2] and parallel[2]

        if not sql_ok:
            raise RuntimeError("synthetic SQL query failed: benchmark is invalid")

        serial_p95 = _percentile(serial_ms, 0.95)
        parallel_p95 = _percentile(parallel_ms, 0.95)
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "benchmark": "paired_sql_rag_readonly_v1",
            "isolated_temp_sqlite": True,
            "network_provider_calls": False,
            "iterations": repeat,
            "warmup": warmup,
            "output_equivalent": equivalent,
            "sql_error_free": sql_ok,
            "serial": {
                "p50_ms": round(statistics.median(serial_ms), 3),
                "p95_ms": serial_p95,
                "samples_ms": serial_ms,
            },
            "parallel": {
                "p50_ms": round(statistics.median(parallel_ms), 3),
                "p95_ms": parallel_p95,
                "samples_ms": parallel_ms,
            },
            "observed_local_p95_ratio": round(serial_p95 / parallel_p95, 3) if parallel_p95 > 0 else None,
            "significance": "NOT_ESTABLISHED",
            "real_concurrent_load_gain": "NOT_MEASURED",
            "real_admissions_quality": "NOT_MEASURED",
            "production_parallelization_authorized": False,
            "decision": "HOLD_FOR_REALISTIC_LOAD_AND_CLAIM_LEVEL_VERIFICATION",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100 or not 0 <= args.warmup <= 20:
        parser.error("repeat must be 1..100, warmup must be 0..20")
    result = _probe(repeat=args.repeat, warmup=args.warmup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["output_equivalent"]:
        raise SystemExit("Experimental parallel result differs from serial")
    print(
        "paired synthetic SQL/RAG:",
        f"serial P95={result['serial']['p95_ms']}ms",
        f"parallel P95={result['parallel']['p95_ms']}ms",
        "production decision=HOLD",
    )


if __name__ == "__main__":
    main()
