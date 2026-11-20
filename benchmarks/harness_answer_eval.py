"""Strict factual answer scorer for synthetic admissions Agent experiments.

A successful finish with a source ID is not enough: model-produced facts must
match independently authored synthetic ground truth AND the tool observation
that owns that exact citation. Free-form prose is deliberately NOT scored.
"""

from __future__ import annotations

import json
from typing import Any


def _parse_answer(answer: str) -> list[dict[str, Any]] | None:
    if not isinstance(answer, str) or len(answer) > 4000:
        return None
    try:
        parsed = json.loads(answer)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"synthetic_demo", "facts"}:
        return None
    if parsed["synthetic_demo"] is not True or not isinstance(parsed["facts"], list):
        return None
    facts = parsed["facts"]
    if not 1 <= len(facts) <= 8 or any(not isinstance(fact, dict) for fact in facts):
        return None
    return facts


def grade_structured_facts(
    answer: str,
    expected_facts: list[dict[str, str]],
    observed_tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Grade a bounded structured answer without exporting free-form content.

    Reviewer-authored `expected_facts` are separate from tool results.
    Each claimed fact must match both its declared oracle and a provenance-
    matching result (same tool, same factual values, source_id in THAT result).
    """
    if not expected_facts:
        return {"status": "NOT_SCORED", "passed": None, "failure_codes": []}
    if not 1 <= len(expected_facts) <= 8:
        raise ValueError("Expected 1..8 independently authored facts")
    if any(not isinstance(item, dict) or "tool" not in item or len(item) < 3 for item in expected_facts):
        raise ValueError("Malformed expected fact oracle")
    claimed = _parse_answer(answer)
    if claimed is None:
        return {"status": "SCORED", "passed": False, "failure_codes": ["answer_contract_invalid"]}
    errors: list[str] = []
    if len(claimed) != len(expected_facts):
        errors.append("fact_count_mismatch")

    unused = list(range(len(expected_facts)))
    for fact in claimed:
        source = fact.get("source_id")
        body = {key: val for key, val in fact.items() if key != "source_id"}
        matching = next((i for i in unused if body == expected_facts[i]), None)
        if matching is None:
            errors.append("fact_mismatch")
            continue
        unused.remove(matching)
        valid_source = any(
            observation.get("tool") == body["tool"]
            and isinstance(observation.get("result"), dict)
            and source in observation["result"].get("source_ids", [])
            and all(observation["result"].get(key) == val for key, val in body.items() if key != "tool")
            for observation in observed_tool_results
            if isinstance(source, str)
        )
        if not valid_source:
            errors.append("citation_not_bound_to_fact")
    if unused:
        errors.append("expected_fact_missing")
    return {"status": "SCORED", "passed": not errors, "failure_codes": sorted(set(errors))}
