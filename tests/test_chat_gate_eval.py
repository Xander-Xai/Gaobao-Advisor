"""Offline chat-gate benchmark contract; never assert real model quality."""

from __future__ import annotations

import pytest

from benchmarks.chat_gate_eval import evaluate_chat_gate
from server.quality.school_score_gate import has_unbound_school_score


def test_offline_chat_gate_frozen_cases():
    report = evaluate_chat_gate()
    assert report["contract"] == "SYNTHETIC_CHAT_GATE_V2"
    assert report["all_contract_cases_passed"], report["case_results"]
    assert report["contract_passed"] == report["cases"]
    assert report["cases"] >= 7
    assert report["real_model_task_success"] == "NOT_MEASURED"
    assert report["provider_cost"] is None
    assert report["production_eligible"] is False


def test_wrong_school_is_now_a_required_negative_control():
    report = evaluate_chat_gate()
    case = next(item for item in report["case_results"] if item["case_id"] == "numeric_wrong_school")
    assert case["passed"]
    assert case["observed_reason_codes"] == ["UNBOUND_SCHOOL_SCORE"]


@pytest.mark.parametrize(
    ("candidate", "blocked"),
    [
        ("根据合成数据，示例甲的最低分为600分。", False),
        ("示例乙的最低分为600分。", True),
        ("示例甲的最低分为610分。", True),
        ("示例甲2025年的最低分为600分。", True),
        ("示例甲的最低分为600分，示例乙的最低分为600分。", True),
        ("根据你提供的600分，还需核实位次。", False),
    ],
)
def test_school_score_gate_requires_exact_row(candidate, blocked):
    state = {
        "slots": {"score": 600},
        "data_query_results": {
            "match_schools": [
                {"school_name": "示例甲（合成）", "min_score": 600},
                {"school_name": "示例乙（合成）", "min_score": 610},
            ]
        },
    }
    assert has_unbound_school_score(state, candidate) is blocked


def test_user_number_and_ambiguous_sql_rows_do_not_authorize_school_score():
    claim = "示例甲的最低分为600分。"
    assert has_unbound_school_score({"slots": {"score": 600}}, claim)
    state = {
        "data_query_results": {
            "match_schools": [
                {"school_name": "示例甲（合成）", "min_score": 600},
                {"school_name": "示例甲（合成）", "min_score": 610},
            ]
        }
    }
    assert has_unbound_school_score(state, claim)
