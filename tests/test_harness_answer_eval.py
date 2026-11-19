"""Adversarial answer evaluation: a citation alone must not imply correctness."""

import json

import pytest

from benchmarks.harness_answer_eval import grade_structured_facts


def setup_case():
    expected = [{"tool": "school_lookup", "school_name": "北辰示例科技大学（合成）", "province": "示例省·甲"}]
    observations = [
        {
            "tool": "school_lookup",
            "result": {
                "school_name": "北辰示例科技大学（合成）",
                "province": "示例省·甲",
                "source_ids": ["synthetic:school:0123456789ab"],
            },
        }
    ]
    return expected, observations


def answer_fact(province="示例省·甲", source="synthetic:school:0123456789ab"):
    return json.dumps(
        {
            "synthetic_demo": True,
            "facts": [
                {
                    "tool": "school_lookup",
                    "school_name": "北辰示例科技大学（合成）",
                    "province": province,
                    "source_id": source,
                }
            ],
        },
        ensure_ascii=False,
    )


def test_correct_fact_with_matching_citation_is_supported():
    expected, observations = setup_case()
    result = grade_structured_facts(answer_fact(), expected, observations)
    assert result == {"status": "SCORED", "passed": True, "failure_codes": []}


def test_wrong_fact_with_valid_source_still_fails():
    expected, observations = setup_case()
    result = grade_structured_facts(answer_fact(province="示例省·丁"), expected, observations)
    assert result["passed"] is False
    assert "fact_mismatch" in result["failure_codes"]


def test_wrong_citation_even_with_correct_answer_fails():
    expected, observations = setup_case()
    result = grade_structured_facts(answer_fact(source="synthetic:school:ffffffffffff"), expected, observations)
    assert result["failure_codes"] == ["citation_not_bound_to_fact"]


def test_unrelated_tool_evidence_does_not_authorize_citation():
    expected, observations = setup_case()
    observations.append(
        {"tool": "major_lookup", "result": {"source_ids": ["synthetic:school:ffffffffffff"]}}
    )
    result = grade_structured_facts(answer_fact(source="synthetic:school:ffffffffffff"), expected, observations)
    assert "citation_not_bound_to_fact" in result["failure_codes"]


def test_extra_factual_claim_fails_even_when_first_fact_correct():
    expected, observations = setup_case()
    answer = json.loads(answer_fact())
    answer["facts"].append(
        {"tool": "school_lookup", "school_name": "任意其他学校", "province": "示例省·甲", "source_id": "fake"}
    )
    result = grade_structured_facts(json.dumps(answer), expected, observations)
    assert result["passed"] is False
    assert "fact_count_mismatch" in result["failure_codes"]


@pytest.mark.parametrize("value", ["不是 JSON", "{}", "[]", json.dumps({"synthetic_demo": False, "facts": []})])
def test_malformed_answer_fails_closed(value):
    expected, observations = setup_case()
    result = grade_structured_facts(value, expected, observations)
    assert result["failure_codes"] == ["answer_contract_invalid"]


def test_out_of_scope_case_does_not_fake_semantic_score():
    assert grade_structured_facts("unknown", [], []) == {
        "status": "NOT_SCORED", "passed": None, "failure_codes": [],
    }
