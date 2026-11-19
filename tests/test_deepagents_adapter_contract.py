"""Deep Agents adapter contracts over REAL synthetic SQL, with FAKE planning.

These tests make no model API calls and therefore are NOT proof of LLM
success. A separate opt-in SDK smoke test validates official Deep Agents APIs.
"""

from __future__ import annotations

import json
import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

dbpath = Path(sys.argv[1])
scenario = sys.argv[2]
os.environ["DATABASE_URL"] = "sqlite:///" + str(dbpath)
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
from scripts.seed_demo_data import DEMO_YEAR, seed_database
seed_database(db_path=dbpath, reset=True)
from server.agent.synthetic_decision_card import DecisionCardProfile
from server.agent.deepagents_adapter import run_deepagents_research

class FakeAgent:
    def __init__(self, tools):
        self.tools = {tool.__name__: tool for tool in tools}
        assert set(self.tools) == {
            "major_lookup", "admission_lookup", "plan_lookup",
            "submit_research", "abstain_with_evidence",
        }
        assert "execute" not in self.tools and "write_file" not in self.tools
    def invoke(self, state, config):
        assert "SYNTHETIC_DEMO" not in str(state.get("files", "")) or state["files"]
        assert config["recursion_limit"] <= 24
        profile = json.loads(state["messages"][0]["content"].split(": ", 1)[1])
        if scenario == "early_submit":
            try:
                self.tools["submit_research"]("[]")
            except ValueError:
                pass
            return {"messages": []}
        ids = []
        row = json.loads(self.tools["major_lookup"](profile["major"]))
        ids.extend(row["source_ids"])
        for school in (profile["school_a"], profile["school_b"]):
            row = json.loads(self.tools["admission_lookup"](school))
            ids.extend(row["source_ids"])
            if row["record_count"] != 1:
                assert self.tools["abstain_with_evidence"]("missing_record") == "SYNTHETIC_ABSTENTION_ACCEPTED"
                return {"messages": []}
        for school in (profile["school_a"], profile["school_b"]):
            row = json.loads(self.tools["plan_lookup"](school))
            ids.extend(row["source_ids"])
            if row["record_count"] != 1:
                assert self.tools["abstain_with_evidence"]("missing_record") == "SYNTHETIC_ABSTENTION_ACCEPTED"
                return {"messages": []}
        assert self.tools["submit_research"](json.dumps(ids)) == "SYNTHETIC_RECEIPT_ACCEPTED"
        return {"messages": []}

def factory(**kwargs):
    assert kwargs["max_model_calls"] == 9
    assert kwargs["virtual_filesystem"] == "StateBackend/read_file_only"
    return FakeAgent(kwargs["tools"])

profile = DecisionCardProfile(
    "北辰示例科技大学（合成）",
    "海岳示例师范大学（合成）" if scenario == "negative" else "沧澜示例农业大学（合成）",
    "计算机科学与技术（合成）",
    "示例省·甲", 550, "物理类", DEMO_YEAR,
)
card = run_deepagents_research(
    profile,
    "openai:fake-contract-model",
    task_id="syn_" + "a" * 32,
    agent_factory=factory,
)
if scenario == "positive":
    assert card["status"] == "evidence_complete_historical_only", card
    assert len(card["schools"]) == 2
    assert len(card["evidence_ids"]) == 5
    assert card["execution"]["tool_calls"] == 5
elif scenario == "negative":
    assert card["status"] == "needs_review", card
    assert card["reason"] == "explicit_abstention"
    assert card["observed_negative_evidence_count"] > 0
    assert card["schools"] == []
else:
    assert card["status"] == "needs_review"
    assert card["schools"] == []
    assert card["reason"] == "unverified_finish"
print(json.dumps({"status": "PASS", "scenario": scenario, "tools": card["execution"]["tool_calls"]}))
"""


def test_five_real_sql_tools_must_obtain_source_bound_receipt_before_business_success(tmp_path):
    for scenario in ("positive", "negative", "early_submit"):
        process = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(tmp_path / f"{scenario}.db"), scenario],
            check=False,
            text=True,
            capture_output=True,
            timeout=100,
        )
        assert process.returncode == 0, process.stderr[-1700:] + process.stdout[-900:]
        assert '"status": "PASS"' in process.stdout
