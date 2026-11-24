"""Offline chat-gate benchmark contract; never assert real model quality."""

from __future__ import annotations

from benchmarks.chat_gate_eval import evaluate_chat_gate


def test_offline_chat_gate_frozen_cases():
    report = evaluate_chat_gate()
    assert report["contract"] == "SYNTHETIC_CHAT_GATE_V1"
    assert report["all_contract_cases_passed"], report["case_results"]
    assert report["contract_passed"] == report["cases"]
    assert report["cases"] >= 7
    assert report["real_model_task_success"] == "NOT_MEASURED"
    assert report["provider_cost"] is None
    assert report["production_eligible"] is False


def test_semantic_mismatch_is_explicit_negative_control():
    report = evaluate_chat_gate()
    limitation = report["known_semantic_attribution_failure"]
    assert limitation["requires_claim_level_verification"] is True
    # A number present in data cannot be treated as proof of attribution to
    # the school named in the answer.
    assert limitation["wrong_school_claim_accepted"] is True
