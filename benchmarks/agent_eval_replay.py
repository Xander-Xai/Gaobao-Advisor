"""Isolated SYNTHETIC_DEMO LangGraph replay; outputs only allowlisted grading data.

Run: python -m benchmarks.agent_eval_replay --out /tmp/gaobao-agent-eval.json
This is a graph contract smoke test, not a model-quality or admissions benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from benchmarks.agent_eval_contract import grade_case, summarize

_DEFAULT_CASES = Path(__file__).parent / "fixtures" / "agent_eval_smoke.json"


@contextmanager
def _block_outbound_network():
    """Refuse outbound sockets, including if a model key is loaded elsewhere."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Agent Eval offline mode forbids network")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


def run_cases(cases: list[dict[str, Any]], invoke: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Grade real or test-injected graph runs without exporting their raw content."""
    records = []
    for index, case in enumerate(cases):
        state = {
            "session_id": f"gaobao-offline-eval-{index:03d}",
            "input_text": case["input_text"],
            "scene": case.get("scene", "general"),
            "slots": case.get("slots", {}),
            "trace": [],
        }
        started = time.perf_counter()
        try:
            raw = invoke(state)
            graded = grade_case(case, raw)
        except Exception:  # noqa: BLE001 - report only a code, never exception detail
            graded = {
                "case_id": case["id"],
                "passed": False,
                "failure_codes": ["graph_run_failed"],
                "observed_node_steps": 0,
                "observed_nodes": [],
                "tool_calls": None,
                "llm_token_usage": None,
            }
        graded["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        records.append(graded)
    return {"summary": summarize(records), "cases": records}


def _read_cases(path: Path) -> list[dict[str, Any]]:
    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, list) or not content:
        raise ValueError("Non-empty JSON case list required")
    if not all(isinstance(c, dict) and c.get("synthetic_demo") is True for c in content):
        raise ValueError("Only explicitly marked synthetic_demo cases are permitted")
    if len(content) > 100:
        raise ValueError("Case count exceeds bounded offline replay budget (100)")
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = _read_cases(args.cases)

    # Import DB and graph modules only after installing a dedicated database URL.
    with tempfile.TemporaryDirectory(prefix="gaobao-agent-eval-") as directory:
        db_path = Path(directory) / "synthetic-eval.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        os.environ["ENABLE_RAG_KB"] = "true"
        with _block_outbound_network():
            from scripts.seed_demo_data import seed_database

            seed_database(db_path=db_path, reset=True)
            from server.graph.graph import build_advisor_graph

            graph = build_advisor_graph()
            report = run_cases(cases, graph.invoke)
            from db.database import engine

            engine.dispose()

    report["environment"] = {"data_profile": "SYNTHETIC_DEMO", "network": "blocked", "model_quality": "NOT_MEASURED"}
    # The target report path is chosen explicitly. No raw graph state is saved.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"cases={report['summary']['cases']} passed={report['summary']['passed']} report={args.out}")
    return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
