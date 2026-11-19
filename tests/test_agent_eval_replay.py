"""Replay adapter tests use a fake invoke, not a real model or database."""

import json
from pathlib import Path

import pytest

from benchmarks.agent_eval_replay import _read_cases, run_cases


def test_replay_grades_actual_invocations_and_redacts_private_data():
    observed = []

    def invoke(state):
        observed.append(state)
        return {
            "scene": state["scene"], "input_text": "private prompt",
            "trace": [{"node": "security_scan", "event": "passed", "secret": "api-key"}],
        }

    cases = [{"id": "fixture-1", "input_text": "private prompt", "scene": "general",
              "expect": {"scene": "general", "required_nodes": ["security_scan"]}}]
    report = run_cases(cases, invoke)
    assert report["summary"]["cases"] == 1
    assert report["summary"]["passed"] == 1
    assert len(observed) == 1
    serialized = json.dumps(report)
    assert "api-key" not in serialized
    assert "private prompt" not in serialized


def test_exception_has_error_code_only():
    def failing(_state):
        raise RuntimeError("password=private")

    report = run_cases([{"id": "x", "input_text": "private", "expect": {"scene": "general"}}], failing)
    assert report["cases"][0]["failure_codes"] == ["graph_run_failed"]
    assert "password" not in str(report)


def test_cases_require_synthetic_flag(tmp_path: Path):
    path = tmp_path / "cases.json"
    path.write_text('[{"id":"x","input_text":"real person","expect":{"scene":"general"}}]')
    with pytest.raises(ValueError, match="synthetic_demo"):
        _read_cases(path)
