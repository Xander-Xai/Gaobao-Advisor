"""Synthetic research: deterministic adaptive tool selection with source binding."""

from __future__ import annotations

from server.agent.readonly_harness import ReadOnlyTool
from server.agent.synthetic_research import research_synthetic_facts

A = "北辰示例科技大学（合成）"
B = "海岳示例师范大学（合成）"
MAJOR = "计算机科学与技术（合成）"
SOURCES = {
    A: "synthetic:school:0123456789ab",
    B: "synthetic:school:abcdef012345",
    MAJOR: "synthetic:major:012345abcdef",
}


def fake_tools(calls: list[str], no_school: bool = False):
    def lookup_school(args):
        name = args["school_name"]
        calls.append("school:" + name)
        if no_school and name == B:
            return {"data_profile": "SYNTHETIC_DEMO", "record_count": 0, "source_ids": []}
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "record_count": 1,
            "school_name": name,
            "province": "示例省·甲",
            "source_ids": [SOURCES[name]],
        }

    def lookup_major(args):
        name = args["major_name"]
        calls.append("major:" + name)
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "record_count": 1,
            "major_name": name,
            "category": "工学",
            "source_ids": [SOURCES[name]],
        }

    return {
        "school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, lookup_school),
        "major_lookup": ReadOnlyTool("major_lookup", {"major_name": str}, lookup_major),
    }


def test_adaptive_research_queries_three_real_tool_implementations_and_cites(monkeypatch):
    calls = []
    monkeypatch.setattr("server.agent.synthetic_research.synthetic_readonly_tools", lambda: fake_tools(calls))
    report = research_synthetic_facts(A, B, MAJOR)
    assert report["status"] == "completed"
    assert report["reason"] == "evidence_bound_finish"
    assert report["planner"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert calls == ["school:" + A, "school:" + B, "major:" + MAJOR]
    assert report["execution"]["tool_calls"] == 3
    assert report["execution"]["model_steps"] == 4
    assert report["execution"]["prompt_tokens"] is None
    assert len(report["facts"]) == 3
    assert set(f["source_id"] for f in report["facts"]) == set(SOURCES.values())
    assert "不得用于真实高考" in report["disclaimer"]


def test_missing_school_does_not_generate_fake_research_completion(monkeypatch):
    calls = []
    monkeypatch.setattr("server.agent.synthetic_research.synthetic_readonly_tools", lambda: fake_tools(calls, True))
    report = research_synthetic_facts(A, B, MAJOR)
    assert report["status"] == "abstained"
    assert report["facts"] == []
    assert calls == ["school:" + A, "school:" + B]
    assert report["execution"]["tool_calls"] == 2
