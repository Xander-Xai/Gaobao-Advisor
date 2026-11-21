"""Cost estimate accepts dated pricing; never upgrades local JSON to provider billing."""

import copy

import pytest

from benchmarks.provider_cost_reconciliation import estimate_synthetic_model_cost
from tests.test_model_run_comparison import fixture


def card():
    return {
        "model_id": "test-provider-model",
        "currency": "USD",
        "as_of_date": "2026-11-20",
        "source_url": "https://example.invalid/pricing",
        "input_usd_per_million_tokens": "2.50",
        "output_usd_per_million_tokens": "10.00",
    }


def test_per_success_estimate_keeps_its_unverified_provenance():
    report = fixture("cost-test")
    result = estimate_synthetic_model_cost(report, card())
    assert result["estimated_cost_usd"] == "0.003600"
    assert result["estimated_cost_per_passing_case_usd"] == "0.000450"
    assert result["declared_invoice_cost_usd"] is None
    assert result["pricing_independently_verified"] is False
    assert result["provider_billing_reconciled"] is False
    assert result["production_deployment_authorized"] is False


def test_matching_invoice_claim_does_not_self_certify_billing():
    report = fixture("bill-claim")
    invoice = {
        "run_id": report["run_id"],
        "model_id": report["model_id"],
        "prompt_tokens": 800,
        "completion_tokens": 160,
        "billed_usd": "0.0042",
    }
    result = estimate_synthetic_model_cost(report, card(), invoice)
    assert result["declared_invoice_cost_usd"] == "0.004200"
    assert result["provider_billing_reconciled"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "http://example.invalid/pricing"),
        ("currency", "CNY"),
        ("input_usd_per_million_tokens", "-1"),
        ("output_usd_per_million_tokens", "NaN"),
        ("as_of_date", "not-a-date"),
    ],
)
def test_bad_or_incomplete_price_snapshot_fail_closed(field, value):
    rate = card()
    rate[field] = value
    with pytest.raises(ValueError):
        estimate_synthetic_model_cost(fixture("bad-rate"), rate)


def test_fabricated_scripted_usage_and_mismatched_claim_rejected():
    report = fixture("bad")
    scripted = copy.deepcopy(report)
    scripted["controller"] = "SCRIPTED_NOT_LLM"
    with pytest.raises(ValueError):
        estimate_synthetic_model_cost(scripted, card())
    invoice = {
        "run_id": report["run_id"],
        "model_id": report["model_id"],
        "prompt_tokens": 801,
        "completion_tokens": 160,
        "billed_usd": "0.0036",
    }
    with pytest.raises(ValueError):
        estimate_synthetic_model_cost(report, card(), invoice)
