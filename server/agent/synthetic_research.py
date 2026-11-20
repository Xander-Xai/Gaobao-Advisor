"""Demo research workflow backed by real synthetic-data tools.

A bounded *deterministic* policy reads tool observations and decides which
information remains missing. This exposes the existing Harness to the API
without implying independent LLM planning or real admissions intelligence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from server.agent.readonly_harness import Decision, HarnessLimits, ReadOnlyTool, run_readonly_agent
from server.agent.readonly_tools import synthetic_readonly_tools

DISCLAIMER = "仅使用虚构合成演示数据；不得用于真实高考填报或院校/专业推荐。"


@dataclass
class SyntheticResearchPlanner:
    school_a: str
    school_b: str
    major: str

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        """Choose the next missing fact from observed data, not a preset step list."""
        findings: dict[tuple[str, str], dict[str, Any]] = {}
        for event in history:
            try:
                observation = json.loads(event["observation"])
            except (KeyError, TypeError, ValueError):
                return Decision("abstain")
            if observation.get("data_profile") != "SYNTHETIC_DEMO":
                return Decision("abstain")
            if observation.get("record_count") != 1 or len(observation.get("source_ids", [])) != 1:
                return Decision("abstain")
            tool = event.get("tool")
            if tool == "school_lookup":
                name = observation.get("school_name")
                attr = observation.get("province")
            elif tool == "major_lookup":
                name = observation.get("major_name")
                attr = observation.get("category")
            else:
                return Decision("abstain")
            if not isinstance(name, str) or not isinstance(attr, str) or not attr:
                return Decision("abstain")
            findings[(tool, name)] = observation

        for school in (self.school_a, self.school_b):
            if ("school_lookup", school) not in findings:
                return Decision("tool", "school_lookup", {"school_name": school})
        if ("major_lookup", self.major) not in findings:
            return Decision("tool", "major_lookup", {"major_name": self.major})

        facts = [
            {
                "tool": "school_lookup",
                "school_name": name,
                "province": findings[("school_lookup", name)]["province"],
                "source_id": findings[("school_lookup", name)]["source_ids"][0],
            }
            for name in (self.school_a, self.school_b)
        ]
        facts.append(
            {
                "tool": "major_lookup",
                "major_name": self.major,
                "category": findings[("major_lookup", self.major)]["category"],
                "source_id": findings[("major_lookup", self.major)]["source_ids"][0],
            }
        )
        return Decision(
            "finish",
            answer=json.dumps({"synthetic_demo": True, "facts": facts}, ensure_ascii=False),
            evidence_ids=[item["source_id"] for item in facts],
        )


def research_synthetic_facts(school_a: str, school_b: str, major: str) -> dict[str, Any]:
    """Return facts + safe execution report; never infer admission probability.

    Caller must enforce auth/feature flag and use only known seeded fictional
    entity names. The Harness never executes shell/network/write operations.
    """
    tools: dict[str, ReadOnlyTool] = synthetic_readonly_tools()
    allowed = {name: tools[name] for name in ("school_lookup", "major_lookup")}
    planner = SyntheticResearchPlanner(school_a, school_b, major)
    result = run_readonly_agent(
        "核验两所虚构学校的省份以及一个虚构专业的学科门类",
        planner,
        allowed,
        HarnessLimits(max_model_steps=4, max_tool_calls=3, max_repeated_call=1, max_context_chars=2400),
    )
    facts: list[dict[str, Any]] = []
    if result.status == "completed":
        try:
            parsed = json.loads(result.answer)
            facts = parsed["facts"]
        except (KeyError, TypeError, ValueError):
            facts = []
    if result.status == "completed" and len(facts) != 3:
        # No success without exactly three evidence-backed synthetic facts.
        return {
            "status": "blocked",
            "reason": "fact_contract_invalid",
            "facts": [],
            "disclaimer": DISCLAIMER,
            "execution": result.public_report(),
            "planner": "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM",
        }
    return {
        "status": result.status,
        "reason": result.reason,
        "facts": facts,
        "disclaimer": DISCLAIMER,
        "execution": result.public_report(),
        "planner": "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM",
    }
