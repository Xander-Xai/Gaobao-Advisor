"""Unit tests for the pure, non-LLM Agent trace grading contract."""

import pytest

from benchmarks.agent_eval_contract import grade_case, summarize


def test_injection_trace_passes_without_leaking_content():
    case = {"id": "synthetic-injection", "expect": {"injection_blocked": True, "required_nodes": ["security_scan"]}}
    result = {
        "input_text": "sensitive input should never appear in artifact",
        "reply": "sensitive response",
        "trace": [{"node": "security_scan", "event": "injection_detected", "secret": "sensitive trace"}],
    }
    grade = grade_case(case, result)
    assert grade["passed"] is True
    assert "sensitive" not in repr(grade)
    assert grade["tool_calls"] is None


def test_missing_required_node_is_a_real_failure():
    grade = grade_case(
        {"id": "synthetic-1", "expect": {"required_nodes": ["rag_retrieve"]}},
        {"trace": []},
    )
    assert grade["passed"] is False
    assert grade["failure_codes"] == ["required_node_missing"]


def test_failed_memory_is_not_mistaken_for_success():
    grade = grade_case(
        {"id": "synthetic-2", "expect": {"scene": "gaokao"}},
        {"scene": "gaokao", "trace": [{"node": "memory_update", "event": "persist_failed", "error": "credentials"}]},
    )
    assert "memory_persistence_failed" in grade["failure_codes"]
    assert "credentials" not in repr(grade)


def test_rag_state_and_structured_card_checks():
    case = {"id": "synthetic-3", "expect": {
        "rag_state": "RAG_ENABLED", "has_rag_evidence": True, "has_structured_card": True,
    }}
    result = {"rag_state": "RAG_ERROR", "rag_evidence": False, "structured_result": {"title": "Card"}}
    grade = grade_case(case, result)
    assert len(grade["failure_codes"]) == 3


def test_summary_never_conflates_nodes_with_tools_or_quality():
    cases = [
        grade_case({"id": "a", "expect": {"scene": "gaokao"}}, {"scene": "gaokao", "trace": [{"node": "a"}]}),
        grade_case({"id": "b", "expect": {"scene": "gaokao"}}, {"scene": "career", "trace": [{"node": "a"}, {"node": "b"}]}),
    ]
    summary = summarize(cases)
    assert summary["cases"] == 2
    assert summary["passed"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["mean_node_steps"] == 1.5
    assert summary["tool_call_count"] == "NOT_MEASURED"
    assert summary["real_admissions_quality"] == "NOT_MEASURED"


def test_duplicate_cases_and_unsupported_rubrics_fail_closed():
    result = grade_case({"id": "a", "expect": {"scene": "gaokao"}}, {"scene": "gaokao"})
    with pytest.raises(ValueError, match="Duplicate"):
        summarize([result, result])
    with pytest.raises(ValueError, match="Unsupported"):
        grade_case({"id": "x", "expect": {"ignore_all_errors": True}}, {})
