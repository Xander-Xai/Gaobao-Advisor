"""Paid eval suite stops after unknown usage or exhausted reported budget."""

from types import SimpleNamespace

import pytest

from benchmarks import readonly_model_eval as evaluation


def cases():
    return [{"id": str(i), "task": "synthetic", "expect": {"tool_sequence": [], "min_evidence": 0}} for i in range(3)]


def install_runner(monkeypatch, prompt, completion):
    limits_seen = []
    monkeypatch.setattr(evaluation, "synthetic_readonly_tools", lambda: [])
    monkeypatch.setattr(evaluation, "_capturing_tools", lambda tools, captured: tools)
    monkeypatch.setattr(evaluation, "grade_structured_facts", lambda *args: {"status": "SCORED", "passed": False})

    def run(task, planner, tools, limits):
        limits_seen.append(limits.max_reported_tokens)
        observed = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "tool_calls": 0,
            "reason": "planner_failed" if prompt is None else "completed",
        }
        return SimpleNamespace(
            status="blocked",
            answer="",
            evidence_ids=[],
            tool_sequence=[],
            prompt_tokens=prompt,
            completion_tokens=completion,
            public_report=lambda: observed,
        )

    monkeypatch.setattr(evaluation, "run_readonly_agent", run)
    return limits_seen


def test_unknown_usage_stops_next_case(monkeypatch):
    seen = install_runner(monkeypatch, None, None)
    report = evaluation.run_model_cases(cases(), object(), max_total_tokens=20)
    assert seen == [20]
    assert report["stop_reason"] == "provider_usage_unknown"
    assert report["prompt_tokens"] is None
    assert report["unexecuted_case_ids"] == ["1", "2"]
    assert report["suite_complete"] is False


def test_remaining_budget_is_applied_per_case(monkeypatch):
    seen = install_runner(monkeypatch, 6, 4)
    report = evaluation.run_model_cases(cases(), object(), max_total_tokens=15)
    assert seen == [15, 5]
    assert report["stop_reason"] == "suite_reported_token_budget_exhausted"
    assert report["unexecuted_case_ids"] == ["2"]
    assert report["prompt_tokens"] == 12
    assert report["completion_tokens"] == 8


@pytest.mark.parametrize("budget", [0, -1, True, 180001])
def test_invalid_budget_rejected(budget):
    with pytest.raises(ValueError, match="max_total_tokens"):
        evaluation.run_model_cases(cases(), object(), max_total_tokens=budget)


def test_incomplete_report_cannot_enter_optimizer(monkeypatch):
    from benchmarks.harness_optimizer import compare_holdout

    install_runner(monkeypatch, 6, 4)
    report = evaluation.run_model_cases(cases(), object(), max_total_tokens=15, groundtruth_review="REVIEW_DECLARED")
    with pytest.raises(ValueError, match="Incomplete"):
        compare_holdout(report, report)


@pytest.mark.parametrize("prompt,completion", [(None, None), (11, 10)])
def test_last_case_unknown_usage_or_overspend_is_not_complete(monkeypatch, prompt, completion):
    from benchmarks.harness_optimizer import compare_holdout

    install_runner(monkeypatch, prompt, completion)
    report = evaluation.run_model_cases(
        cases()[:1], object(), max_total_tokens=20, groundtruth_review="REVIEW_DECLARED"
    )
    assert report["cases"] == report["planned_cases"] == 1
    assert report["unexecuted_case_ids"] == []
    assert report["suite_complete"] is False
    with pytest.raises(ValueError, match="Incomplete"):
        compare_holdout(report, report)


def test_exact_budget_on_final_case_is_complete(monkeypatch):
    install_runner(monkeypatch, 11, 9)
    report = evaluation.run_model_cases(cases()[:1], object(), max_total_tokens=20)
    assert report["suite_complete"] is True
    assert report["prompt_tokens"] + report["completion_tokens"] == 20


def test_empty_suite_is_not_complete(monkeypatch):
    seen = install_runner(monkeypatch, 6, 4)
    report = evaluation.run_model_cases([], object())
    assert seen == []
    assert report["suite_complete"] is False
