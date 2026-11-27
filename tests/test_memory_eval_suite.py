"""The controlled memory-strategy comparison and its evidence boundaries."""

from __future__ import annotations

import json

import pytest

from benchmarks.memory_eval import ARMS, CONTRACT, load_cases, run_evaluation


@pytest.fixture(scope="module")
def report():
    return run_evaluation()


def test_frozen_case_set_covers_every_required_scenario():
    cases = load_cases()
    scenarios = {case["scenario"] for case in cases}
    assert scenarios == {
        "多轮条件理解",
        "指代消解",
        "城市与专业偏好延续",
        "用户修改偏好",
        "跨会话记忆",
        "错误记忆纠正",
        "用户撤销授权",
        "用户间记忆泄漏",
        "过期记忆",
        "摘要事实丢失",
        "记忆中的 Prompt Injection",
        "记忆与真实 SQL 数据冲突",
    }


def test_report_keeps_evidence_classes_apart(report):
    assert report["contract"] == CONTRACT
    assert report["evidence_class"] == "SYNTHETIC"
    assert report["real_model_evidence"] == "NOT_MEASURED"
    assert report["manual_review"]["status"] == "MANUAL_REVIEW_PENDING"
    assert all(item["reviewer_verdict"] is None for item in report["manual_review"]["items"])


def test_every_arm_is_measured_on_every_case(report):
    assert tuple(report["arms"]) == ARMS
    for arm in ARMS:
        rows = [row for row in report["per_case"] if row["arm"] == arm]
        assert len(rows) == report["case_count"] * 2  # cold pass and warm pass


def test_no_arm_leaks_another_users_or_expired_memory(report):
    for row in report["per_case"]:
        assert row["forbidden_leaks"] == [], row


def test_current_sql_evidence_survives_every_arm(report):
    for row in report["per_case"]:
        assert row["sql_evidence_missing"] == [], row


def test_snapshot_reuse_avoids_a_second_summary_call(report):
    summary = report["summary"]
    assert summary["incremental_snapshot"]["summarizer_calls_first_pass"] > 0
    assert summary["incremental_snapshot"]["summarizer_calls_second_pass"] == 0
    # A per-request summary is paid again on every request.
    assert summary["per_request_summary"]["summarizer_calls_second_pass"] > 0
    assert summary["no_summary"]["summarizer_calls_second_pass"] == 0
    assert summary["sliding_window"]["summarizer_calls_second_pass"] == 0


def test_trade_off_between_token_cost_and_fact_retention(report):
    summary = report["summary"]
    # The silent sliding window is the cheapest arm and it does lose facts.
    assert summary["sliding_window"]["context_tokens_mean"] < summary["no_summary"]["context_tokens_mean"]
    assert summary["sliding_window"]["key_fact_retention_rate"] < summary["no_summary"]["key_fact_retention_rate"]
    # The summary arms keep every synthetic fact at a lower token cost.
    assert summary["per_request_summary"]["key_fact_retention_rate"] == 1.0
    assert summary["per_request_summary"]["context_tokens_mean"] < summary["no_summary"]["context_tokens_mean"]
    assert summary["incremental_snapshot"]["key_fact_retention_rate"] == 1.0


def test_report_is_json_serialisable_and_self_describing(report):
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert CONTRACT in payload
    assert "SYNTHETIC_DEMO" in payload
