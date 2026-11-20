"""Inspect/patch/test workflow tests; real Docker execution runs in container CI."""

import pytest

import benchmarks.sandbox_coding_agent as coding
from benchmarks.sandbox_coding_agent import CodingAction, ScriptedRepairPlanner, run_repair

IMAGE = "sha256:" + "a" * 64


class Actions:
    def __init__(self, values):
        self.values = iter(values)

    def decide(self, task, observations):
        return next(self.values)


@pytest.fixture
def fake_docker(monkeypatch):
    """Only unit tests use this mock; GitHub's container job uses actual Docker."""

    def run(_image, workspace):
        source = (workspace / "solution.py").read_text(encoding="utf-8")
        correct = "return a + b" in source or "return max(low, min(high, value))" in source
        if correct:
            (workspace / "artifact.txt").write_text("PASS", encoding="utf-8")
        return {"passed": correct}

    monkeypatch.setattr(coding, "run_sandbox_probe", run)


def test_two_synthetic_bugs_are_repaired_in_isolated_workflow(fake_docker):
    for task_id in ("addition", "clamp"):
        spec = coding._TASKS[task_id]
        result = run_repair(task_id, IMAGE, ScriptedRepairPlanner(spec["correct"]))
        assert result["status"] == "completed"
        assert result["tool_sequence"] == ["inspect", "test", "edit", "test"]
        assert result["tool_calls"] == 4
        assert result["test_runs"] == 2
        assert result["reason"] == "sandbox_tests_passed"
        assert result["persistent_memory"] is False


def test_finish_without_test_cannot_claim_success(fake_docker):
    result = run_repair("addition", IMAGE, Actions([CodingAction("finish")]))
    assert result["reason"] == "unverified_finish"
    assert result["status"] == "blocked"


def test_patch_after_success_invalidates_previous_test(fake_docker):
    result = run_repair(
        "addition",
        IMAGE,
        Actions(
            [
                CodingAction("edit", "def add(a, b):\n    return a + b\n"),
                CodingAction("test"),
                CodingAction("edit", "def add(a, b):\n    return a - b\n"),
                CodingAction("finish"),
            ]
        ),
    )
    assert result["reason"] == "unverified_finish"


def test_duplicate_patch_does_not_enter_execution_loop(fake_docker):
    source = "def add(a, b):\n    return a + b\n"
    result = run_repair("addition", IMAGE, Actions([CodingAction("edit", source), CodingAction("edit", source)]))
    assert result["reason"] == "duplicate_patch"


def test_syntax_error_cannot_be_executed(fake_docker):
    result = run_repair("addition", IMAGE, Actions([CodingAction("edit", "def broken(\n")]))
    assert result["reason"] == "patch_syntax_error"
    assert result["test_runs"] == 0


@pytest.mark.parametrize(
    "malicious_source",
    [
        "import os\ndef add(a, b):\n    return a + b\n",
        "def add(a, b):\n    import os\n    os._exit(0)\n",
        "def add(a, b):\n    return __import__('os').system('id')\n",
        "def add(a, b):\n    return print('SYNTHETIC_WORKSPACE_PASS')\n",
        "def add(a, b):\n    return open('/workspace/probe.py','w')\n",
        "def add(a, b):\n    return a + b\ndef probe():\n    return 1\n",
    ],
)
def test_untrusted_patch_cannot_bypass_oracle_via_file_or_process_side_effects(fake_docker, malicious_source):
    result = run_repair("addition", IMAGE, Actions([CodingAction("edit", malicious_source)]))
    assert result["reason"] == "unsafe_patch_ast"
    assert result["tool_calls"] == 0
    assert result["test_runs"] == 0


def test_step_budget_stops_unending_inspection(fake_docker):
    result = run_repair("addition", IMAGE, Actions([CodingAction("inspect")] * 12), max_steps=3)
    assert result["reason"] == "step_budget_exhausted"
    assert result["model_steps"] == 3
