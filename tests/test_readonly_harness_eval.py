"""Real synthetic-data tool execution and 30-case safety workflow replay."""

import json
import subprocess
import sys

import pytest

from benchmarks.readonly_harness_eval import fact_oracle_matches, load_suite
from server.agent.readonly_tools import synthetic_readonly_tools


def test_only_four_explicit_readonly_tools_exposed():
    tools = synthetic_readonly_tools()
    assert set(tools) == {"school_lookup", "major_lookup", "candidate_lookup", "knowledge_lookup"}
    assert all(callable(tool.handler) and tool.parameters for tool in tools.values())


def test_offline_suite_has_30_independent_declared_oracles():
    cases = load_suite()
    assert len(cases) == 30
    assert len({case["id"] for case in cases}) == 30
    assert sum(bool(case.get("expected_facts")) for case in cases) == 20
    assert sum(case["expect"]["status"] == "completed" for case in cases) == 22
    assert sum(case["expect"]["status"] == "blocked" for case in cases) == 8


def test_independent_fact_oracle_rejects_plausible_but_wrong_school_location():
    expected = [{"tool": "school_lookup", "school_name": "北辰示例科技大学（合成）", "province": "示例省·甲"}]
    correct = [{"tool": "school_lookup", "result": {"school_name": "北辰示例科技大学（合成）",
                                                   "province": "示例省·甲"}}]
    wrong = [{"tool": "school_lookup", "result": {"school_name": "北辰示例科技大学（合成）",
                                                 "province": "示例省·丁"}}]
    assert fact_oracle_matches(expected, correct)
    assert not fact_oracle_matches(expected, wrong)


def test_duplicate_id_fails_before_agent_execution(tmp_path):
    path = tmp_path / "invalid.json"
    sample = load_suite()[0]
    path.write_text(json.dumps([sample, sample], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(path)


def test_real_readonly_harness_30_case_eval_in_clean_subprocess(tmp_path):
    output = tmp_path / "harness-report.json"
    p = subprocess.run(
        [sys.executable, "-m", "benchmarks.readonly_harness_eval", "--out", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert output.exists(), f"Offline replay produced no artifact, exit={p.returncode}"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["cases"] == 30
    assert report["controller"] == "SCRIPTED_NOT_LLM"
    assert report["provider_token_usage"] == "NOT_MEASURED"
    assert report["independent_model_quality"] == "NOT_MEASURED"
    assert report["policy_contract_passed"] == 30, [
        (row["case_id"], row["observed"]["reason"]) for row in report["cases_detail"] if not row["passed"]
    ]
    assert p.returncode == 0
    serialized = json.dumps(report, ensure_ascii=False)
    assert "api_key" not in serialized.lower()
    assert "school_name" not in serialized
    assert "synthetic_task" not in serialized
