"""Offline, privacy-minimizing grading contract for Gaobao Advisor graph traces.

This module grades supplied *observations*; it never invokes an LLM, claims
business success, or persists user prompts. Node visits are not tool calls.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

_ALLOWED_EXPECTATIONS = frozenset(
    {
        "scene",
        "required_nodes",
        "forbidden_nodes",
        "has_missing_fields",
        "injection_blocked",
        "rag_state",
        "has_rag_evidence",
        "has_structured_card",
    }
)


def grade_case(case: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    """Grade one graph result against an explicit deterministic rubric.

    Do not pass a complete user trace to a report. Output is allowlisted to
    case ID, booleans, node names and failure codes; no prompt, slot, reply,
    source text, model output or exception message is copied.
    """
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("Case must have a non-empty string id")
    expects = case.get("expect")
    if not isinstance(expects, dict) or not expects:
        raise ValueError("Case must have a non-empty expect object")
    unknown = set(expects).difference(_ALLOWED_EXPECTATIONS)
    if unknown:
        raise ValueError("Unsupported expectation keys")
    if not isinstance(observation, dict):
        raise ValueError("Observation must be an object")

    trace = observation.get("trace", [])
    if not isinstance(trace, list):
        trace = []
    events = [t for t in trace if isinstance(t, dict)]
    nodes = [t.get("node") for t in events if isinstance(t.get("node"), str)]
    node_set = set(nodes)
    failures: list[str] = []

    if "scene" in expects and observation.get("scene") != expects["scene"]:
        failures.append("scene_mismatch")
    for node in expects.get("required_nodes", []):
        if node not in node_set:
            failures.append("required_node_missing")
    for node in expects.get("forbidden_nodes", []):
        if node in node_set:
            failures.append("forbidden_node_present")
    if "has_missing_fields" in expects:
        if bool(observation.get("missing_fields")) != expects["has_missing_fields"]:
            failures.append("missing_field_contract")
    if "injection_blocked" in expects:
        blocked = any(t.get("node") == "security_scan" and t.get("event") == "injection_detected" for t in events)
        if blocked != expects["injection_blocked"]:
            failures.append("injection_gate_contract")
    if "rag_state" in expects and observation.get("rag_state") != expects["rag_state"]:
        failures.append("rag_state_mismatch")
    if "has_rag_evidence" in expects:
        if bool(observation.get("rag_evidence")) != expects["has_rag_evidence"]:
            failures.append("rag_evidence_contract")
    if "has_structured_card" in expects:
        card = observation.get("structured_result")
        has_card = isinstance(card, dict) and bool(card.get("title")) and bool(card.get("scene"))
        if has_card != expects["has_structured_card"]:
            failures.append("structured_card_contract")
    if any(t.get("event") == "persist_failed" for t in events):
        failures.append("memory_persistence_failed")

    return {
        "case_id": case_id,
        "passed": not failures,
        "failure_codes": sorted(set(failures)),
        "observed_node_steps": len(nodes),
        "observed_nodes": nodes,
        "tool_calls": None,
        "llm_token_usage": None,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only graded cases. Unknown metrics remain explicitly unknown."""
    identifiers = [r["case_id"] for r in results]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate case IDs are not allowed")
    n = len(results)
    passed = sum(r["passed"] is True for r in results)
    counts = Counter(code for r in results for code in r["failure_codes"])
    steps = sorted(int(r["observed_node_steps"]) for r in results)
    p95_idx = max(0, (95 * n + 99) // 100 - 1) if n else 0
    return {
        "suite": "synthetic_graph_contract",
        "cases": n,
        "passed": passed,
        "pass_rate": round(passed / n, 4) if n else None,
        "mean_node_steps": round(sum(steps) / n, 2) if n else None,
        "p95_node_steps": steps[p95_idx] if n else None,
        "failures_by_code": dict(sorted(counts.items())),
        "tool_call_count": "NOT_MEASURED",
        "token_usage": "NOT_MEASURED",
        "end_to_end_task_success": "NOT_MEASURED",
        "real_admissions_quality": "NOT_MEASURED",
    }
