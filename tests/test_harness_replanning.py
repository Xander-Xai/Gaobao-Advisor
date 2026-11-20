"""Opt-in replan after a retry-safe synthetic tool fault; no provider calls."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.agent.readonly_harness import Decision, HarnessLimits, ReadOnlyTool, run_readonly_agent
from server.agent.synthetic_checkpoint import SQLiteSyntheticCheckpointStore

SOURCE = "synthetic:school:0123456789ab"
TASK = "核验虚构院校（仅 SYNTHETIC_DEMO）"


class SequencePlanner:
    def __init__(self, steps: list[Decision]) -> None:
        self.steps = iter(steps)
        self.histories: list[list[dict]] = []

    def decide(self, task, history, schemas):
        self.histories.append(history)
        return next(self.steps)


def _tool(handler, *, retry_safe=True):
    return {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, handler, retry_safe=retry_safe)}


def _tool_choice():
    return Decision("tool", "school_lookup", {"school_name": "虚构甲大学"})


def _finish():
    return Decision("finish", answer="仅供合成数据回放", evidence_ids=[SOURCE])


def _good_result(_args):
    return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [SOURCE], "school_name": "虚构甲大学"}


def test_transient_fault_replans_once_and_uses_only_fresh_evidence():
    calls = []

    def intermittent(args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("DO_NOT_LEAK_PRIVATE_PROVIDER_KEY")
        return _good_result(args)

    planner = SequencePlanner([_tool_choice(), _tool_choice(), _finish()])
    result = run_readonly_agent(
        TASK,
        planner,
        _tool(intermittent),
        HarnessLimits(max_recoverable_tool_failures=1),
    )
    assert result.status == "completed"
    assert result.reason == "evidence_bound_finish"
    assert (result.model_steps, result.tool_calls, result.tool_failures) == (3, 2, 1)
    assert result.trace[0]["result"] == "recoverable_tool_failure"
    assert result.trace[0]["observed_source_count"] == 0
    assert result.trace[1]["result"] == "ok"
    assert planner.histories[1][0]["source_ids"] == []
    assert "retryable_tool_failure" in planner.histories[1][0]["observation"]
    assert "DO_NOT_LEAK" not in str(result.public_report())
    assert "DO_NOT_LEAK" not in str(planner.histories)


@pytest.mark.parametrize(
    ("retry_safe", "budget"),
    [(False, 1), (True, 0), (False, 0)],
)
def test_failure_never_retries_without_two_explicit_permissions(retry_safe, budget):
    def explode(_args):
        raise RuntimeError("never emit this text")

    result = run_readonly_agent(
        TASK,
        SequencePlanner([_tool_choice(), _tool_choice()]),
        _tool(explode, retry_safe=retry_safe),
        HarnessLimits(max_recoverable_tool_failures=budget),
    )
    assert result.status == "blocked"
    assert result.reason == "tool_execution_failed"
    assert result.tool_calls == 1
    assert "never emit" not in str(result.public_report())


def test_recovery_budget_exceeded_blocks_without_inventing_success():
    def explode(_args):
        raise RuntimeError("temporary")

    result = run_readonly_agent(
        TASK,
        SequencePlanner([_tool_choice(), _tool_choice(), _finish()]),
        _tool(explode),
        HarnessLimits(max_recoverable_tool_failures=1),
    )
    assert result.status == "blocked"
    assert result.reason == "tool_execution_failed"
    assert result.tool_calls == 2
    assert result.tool_failures == 2
    assert result.evidence_ids == []
    assert [e["result"] for e in result.trace] == ["recoverable_tool_failure", "tool_execution_failed"]


def test_handler_cannot_spoof_retryable_error():
    def forged(_args):
        return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [], "retryable_tool_failure": True}

    result = run_readonly_agent(
        TASK,
        SequencePlanner([_tool_choice(), _finish()]),
        _tool(forged),
        HarnessLimits(max_recoverable_tool_failures=1),
    )
    assert result.status == "blocked"
    assert result.reason == "tool_evidence_invalid"


def test_checkpoint_restores_sanitized_fault_and_retries_in_new_process(tmp_path: Path):
    journal = SQLiteSyntheticCheckpointStore(tmp_path / "synthetic-replan.sqlite")
    run_id = "synthetic-run-recovery-fixture"
    state = {"calls": 0}

    def intermittent(args):
        state["calls"] += 1
        if state["calls"] == 1:
            raise sqlite3.OperationalError("PRIVATE_DB_PATH")
        return _good_result(args)

    limits = HarnessLimits(max_recoverable_tool_failures=1)
    failed = run_readonly_agent(
        TASK,
        SequencePlanner([_tool_choice()]),  # next decision raises StopIteration
        _tool(intermittent),
        limits,
        checkpoint=journal,
        run_id=run_id,
    )
    assert failed.reason == "planner_failed"
    resumed_planner = SequencePlanner([_tool_choice(), _finish()])
    resumed = run_readonly_agent(
        TASK, resumed_planner, _tool(intermittent), limits, checkpoint=journal, run_id=run_id
    )
    assert resumed.status == "completed"
    assert resumed.model_steps == 3
    assert resumed.tool_calls == 2
    assert resumed.tool_failures == 1
    assert resumed_planner.histories[0][0]["source_ids"] == []
    assert state["calls"] == 2
    assert "PRIVATE_DB_PATH" not in str(resumed.public_report())
    again = run_readonly_agent(
        TASK, SequencePlanner([]), _tool(intermittent), limits, checkpoint=journal, run_id=run_id
    )
    assert again.status == "completed"  # idempotent terminal read, no additional call
    assert state["calls"] == 2


def test_recovery_budget_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        HarnessLimits(max_recoverable_tool_failures=6, max_tool_calls=5)
    with pytest.raises(ValueError):
        HarnessLimits(max_recoverable_tool_failures=True)
