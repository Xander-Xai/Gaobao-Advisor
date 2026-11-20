"""Real cross-process LangGraph SQLite checkpoint: no model API calls.

Unlike prior TaskRun replay tests, this restores internal StateGraph state from
a persisted checkpoint and resumes only the pending node. It does NOT prove
safe resumption of the Deep Agents model/tool-observation ledger.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")

_SCRIPT = r"""
import os
import sys
from pathlib import Path
from typing import TypedDict

root = Path(sys.argv[1])
phase = sys.argv[2]
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "taskruns.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
if phase == "crash":
    seed_database(db_path=root / "admissions.sqlite", reset=True)

from server.agent.synthetic_decision_card import DecisionCardProfile
from server.agent.deepagents_checkpoint import open_synthetic_deepagents_checkpointer
from langgraph.graph import StateGraph, START, END

profile = DecisionCardProfile(
    school_a=DEMO_SCHOOLS[0][0],
    school_b=DEMO_SCHOOLS[6][0],
    major=DEMO_MAJORS[0][0],
    province="示例省·甲",
    score=550,
    subject_type="物理类",
    year=DEMO_YEAR,
)
task_id = "syn_" + "b" * 32
path = root / "deepagents.sqlite"
marker = root / "first-node-executions.txt"
class State(TypedDict, total=False):
    data_profile: str
    steps: int
    stage: str

def first(state: State) -> State:
    with marker.open("a", encoding="utf-8") as fh:
        fh.write("first\n")
    return {"steps": state["steps"] + 1, "stage": "first-durable"}

def second(state: State) -> State:
    if phase == "crash":
        os._exit(17)
    return {"steps": state["steps"] + 1, "stage": "second-durable"}

builder = StateGraph(State)
builder.add_node("first", first)
builder.add_node("second", second)
builder.add_edge(START, "first")
builder.add_edge("first", "second")
builder.add_edge("second", END)
config = {"configurable": {"thread_id": task_id, "checkpoint_ns": ""}}

with open_synthetic_deepagents_checkpointer(path, task_id, profile, "openai:synthetic-test", "a" * 64) as (
    saver, prior
):
    compiled = builder.compile(checkpointer=saver)
    if phase == "crash":
        assert prior is False
        compiled.invoke({"data_profile": "SYNTHETIC_DEMO", "steps": 0}, config=config)
        raise AssertionError("Expected the worker process to terminate")
    else:
        assert prior is True
        paused = compiled.get_state(config)
        assert tuple(paused.next) == ("second",), paused.next
        assert paused.values["stage"] == "first-durable"
        resumed = compiled.invoke(None, config=config)
        assert resumed["steps"] == 2
        assert resumed["stage"] == "second-durable"
        assert marker.read_text(encoding="utf-8") == "first\n"
        assert compiled.get_state(config).next == ()

if phase == "resume":
    # The same TaskRun ID must never be reused for a different model contract.
    try:
        with open_synthetic_deepagents_checkpointer(path, task_id, profile, "openai:different-test", "a" * 64):
            pass
    except ValueError:
        pass
    else:
        raise AssertionError("A changed model reused a previous checkpoint")

    # The graph checkpoint file may not overlap with the journal or input data.
    try:
        with open_synthetic_deepagents_checkpointer(
            root / "admissions.sqlite", task_id, profile, "openai:synthetic-test", "a" * 64
        ):
            pass
    except ValueError:
        pass
    else:
        raise AssertionError("Admissions database was accepted as checkpoint store")
    try:
        with open_synthetic_deepagents_checkpointer(
            root / "taskruns.sqlite", task_id, profile, "openai:synthetic-test", "a" * 64
        ):
            pass
    except ValueError:
        pass
    else:
        raise AssertionError("TaskRun journal was accepted as checkpoint store")

    os.environ["APP_ENV"] = "production"
    try:
        with open_synthetic_deepagents_checkpointer(path, task_id, profile, "openai:synthetic-test", "a" * 64):
            pass
    except ValueError:
        pass
    else:
        raise AssertionError("Production access unexpectedly accepted")
    print("langgraph_persistent_cross_process_resume=1/1")
"""


def test_langgraph_sqlite_persists_and_resumes_only_pending_node(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["APP_ENV"] = "development"
    cmd = [sys.executable, "-c", _SCRIPT, str(tmp_path)]
    first = subprocess.run([*cmd, "crash"], capture_output=True, text=True, timeout=45, check=False, env=env)
    assert first.returncode == 17, first.stderr[-2200:] + first.stdout[-500:]
    resumed = subprocess.run([*cmd, "resume"], capture_output=True, text=True, timeout=45, check=False, env=env)
    assert resumed.returncode == 0, resumed.stderr[-2500:] + resumed.stdout[-500:]
    assert "langgraph_persistent_cross_process_resume=1/1" in resumed.stdout
    assert (tmp_path / "first-node-executions.txt").read_text(encoding="utf-8") == "first\n"


def test_official_deepagents_constructor_accepts_private_sqlite_checkpointer(monkeypatch, tmp_path):
    """Actual SDK graph construction only, deliberately no model invocation."""
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from deepagents.backends import StateBackend
    from deepagents.middleware import FilesystemMiddleware
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain_openai import ChatOpenAI

    from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
    from server.agent.deepagents_checkpoint import open_synthetic_deepagents_checkpointer
    from server.agent.synthetic_decision_card import DecisionCardProfile

    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "fictional.sqlite"))
    monkeypatch.setenv("SYNTHETIC_TASKRUN_JOURNAL", str(tmp_path / "journal.sqlite"))
    model_id = "openai:synthetic-checkpoint-constructor"
    register_harness_profile(
        model_id,
        HarnessProfile(
            excluded_tools=frozenset(
                {"ls", "glob", "grep", "write_file", "edit_file", "delete", "execute", "task", "write_todos"}
            ),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    profile = DecisionCardProfile(
        school_a=DEMO_SCHOOLS[0][0],
        school_b=DEMO_SCHOOLS[6][0],
        major=DEMO_MAJORS[0][0],
        province="示例省·甲",
        score=550,
        subject_type="物理类",
        year=DEMO_YEAR,
    )
    backend = StateBackend()
    model = ChatOpenAI(model=model_id.removeprefix("openai:"), api_key="synthetic-no-network-call", max_retries=0)
    with open_synthetic_deepagents_checkpointer(
        tmp_path / "checkpoint.sqlite", "syn_" + "c" * 32, profile, model_id, "e" * 64
    ) as (saver, prior):
        assert prior is False
        graph = create_deep_agent(
            model=model,
            tools=[],
            system_prompt="No network calls; synthetic checkpoint constructor.",
            backend=backend,
            middleware=[
                FilesystemMiddleware(backend=backend, tools=["read_file"]),
                ModelCallLimitMiddleware(run_limit=9, exit_behavior="end"),
            ],
            skills=["/skills/"],
            checkpointer=saver,
            subagents=[],
        )
        assert callable(graph.invoke)
        assert "tools" in graph.get_graph().nodes
