"""Real 2-process Graph checkpoint + durable SQL receipt recovery, no LLM API.

The injected graph is deterministic LangGraph, NOT a live Deep Agents model.
This explicitly tests rehydration, tool-key replay, SQL evidence and eventual
business receipt. Full Deep Agents model token/effect recovery is NOT asserted.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")

_SCRIPT = r"""
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, TypedDict

root = Path(sys.argv[1])
phase = sys.argv[2]
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "journal.sqlite")
os.environ["SYNTHETIC_DEEPAGENTS_CHECKPOINT_DB"] = str(root / "graph-and-ledger.sqlite")
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["APP_ENV"] = "development"
if phase == "crash":
    from scripts.seed_demo_data import seed_database
    seed_database(db_path=root / "admissions.sqlite", reset=True)

from scripts.seed_demo_data import DEMO_YEAR
from server.agent.synthetic_decision_card import DecisionCardProfile
from server.agent.deepagents_adapter import run_deepagents_research, _SYSTEM, _SKILL_PATH
from server.agent.deepagents_tool_ledger import SyntheticDeepAgentsToolLedger, UnsafeToolReplay
from langgraph.graph import StateGraph, START, END

profile = DecisionCardProfile(
    "北辰示例科技大学（合成）",
    "沧澜示例农业大学（合成）",
    "计算机科学与技术（合成）",
    "示例省·甲", 550, "物理类", DEMO_YEAR
)
model_id = "openai:fake-frozen-no-provider"
task_id = "syn_" + "d" * 32
first_log = root / "first-node.txt"

class GraphState(TypedDict, total=False):
    messages: list[Any]
    files: dict[str, Any]
    source_ids: list[str]
    marker: str

def fake_graph_factory(**kwargs):
    assert kwargs["virtual_filesystem"] == "StateBackend/read_file_only"
    assert kwargs["max_model_calls"] == 9
    assert kwargs["checkpointer"] is not None
    assert kwargs["resume"] is (phase == "resume")
    tools = {tool.__name__:tool for tool in kwargs["tools"]}
    def first(state: GraphState) -> dict[str, Any]:
        with first_log.open("a", encoding="utf-8") as out:
            out.write("first\n")
        sources = []
        for tool, subject in (
            ("major_lookup", profile.major),
            ("admission_lookup", profile.school_a),
        ):
            found = json.loads(tools[tool](subject))
            sources.extend(found["source_ids"])
        return {"source_ids": sources, "marker": "first-checkpointed"}
    def second(state: GraphState) -> dict[str, Any]:
        if phase == "crash":
            os._exit(17)
        assert state["marker"] == "first-checkpointed"
        assert len(state["source_ids"]) == 2
        # A single replay of a tool committed before the crash must return
        # the same verified row WITHOUT creating a second SQL ledger event.
        old = json.loads(tools["major_lookup"](profile.major))
        assert old["source_ids"][0] == state["source_ids"][0]
        sources = list(state["source_ids"])
        for tool, subject in (
            ("admission_lookup", profile.school_b),
            ("plan_lookup", profile.school_a),
            ("plan_lookup", profile.school_b),
        ):
            found = json.loads(tools[tool](subject))
            sources.extend(found["source_ids"])
        assert len(sources) == 5
        assert tools["submit_research"](json.dumps(sources)) == "SYNTHETIC_RECEIPT_ACCEPTED"
        return {"source_ids": sources, "marker": "complete"}
    graph = StateGraph(GraphState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    compiled = graph.compile(checkpointer=kwargs["checkpointer"])
    if phase == "resume":
        state = compiled.get_state({"configurable": {"thread_id": task_id}})
        assert state.next == ("second",), state.next
    return compiled

if phase in {"crash", "resume"}:
    result = run_deepagents_research(
        profile, model_id, task_id=task_id, agent_factory=fake_graph_factory
    )
    if phase == "crash":
        raise AssertionError("First process did not hard-crash")
    assert result["status"] == "evidence_complete_historical_only", result
    assert len(result["schools"]) == 2
    assert len(result["evidence_ids"]) == 5
    assert result["execution"]["tool_calls"] == 5
    assert result["execution"]["external_model_executed"] is False
    assert first_log.read_text(encoding="utf-8") == "first\n"
    import hashlib
    contract = hashlib.sha256((_SYSTEM + _SKILL_PATH.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
    ledger = SyntheticDeepAgentsToolLedger(
        root / "graph-and-ledger.sqlite", task_id, profile, model_id, contract
    )
    with sqlite3.connect(root / "graph-and-ledger.sqlite") as conn:
        events = conn.execute(
            "SELECT event_kind, COUNT(*) FROM synthetic_deepagents_tool_ledger "
            "WHERE task_id=? GROUP BY event_kind", (task_id,)
        ).fetchall()
    assert sorted(events) == [("decision", 1), ("tool", 5)], events
    assert ledger is not None
    print("durable_synthetic_agent_receipt_resume=1/1")
elif phase == "source-changed":
    with sqlite3.connect(root / "admissions.sqlite") as db:
        db.execute(
            "UPDATE admission_scores SET min_score=min_score+1 "
            "WHERE school_id IN (SELECT id FROM schools WHERE name=?)",
            (profile.school_a,),
        )
    # Resume path must refuse stale SQL BEFORE invoking any graph nodes.
    try:
        run_deepagents_research(
            profile, model_id, task_id=task_id, agent_factory=fake_graph_factory
        )
    except (UnsafeToolReplay, RuntimeError, ValueError):
        pass
    else:
        raise AssertionError("Changed admissions SQL was accepted on recovery")
    assert first_log.read_text(encoding="utf-8") == "first\n"
    print("stale_sql_receipt_rejected=1/1")
else:
    raise AssertionError("Unknown synthetic phase")
"""


def _run(root, phase):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["APP_ENV"] = "development"
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(root), phase],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
        check=False,
    )


def test_tool_observation_resume_with_real_sql_and_real_langgraph(tmp_path):
    crashed = _run(tmp_path, "crash")
    assert crashed.returncode == 17, crashed.stderr[-2600:]
    recovered = _run(tmp_path, "resume")
    assert recovered.returncode == 0, recovered.stderr[-3000:] + recovered.stdout[-1000:]
    assert "durable_synthetic_agent_receipt_resume=1/1" in recovered.stdout
    assert (tmp_path / "first-node.txt").read_text(encoding="utf-8") == "first\n"


def test_changed_sql_source_blocks_durable_resume(tmp_path):
    crashed = _run(tmp_path, "crash")
    assert crashed.returncode == 17, crashed.stderr[-2600:]
    stale = _run(tmp_path, "source-changed")
    assert stale.returncode == 0, stale.stderr[-2600:] + stale.stdout[-900:]
    assert "stale_sql_receipt_rejected=1/1" in stale.stdout
