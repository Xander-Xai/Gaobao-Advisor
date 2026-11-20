"""Offline cost estimates for versioned synthetic model eval reports.

A local rate card or claimed invoice is not provider authentication. Never
convert self-declared usage into an independently reconciled billing claim.
No network, API keys, raw prompts or student data are consumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchmarks.model_run_comparison import _validate

_MILLION = Decimal(1_000_000)
_QUANTUM = Decimal("0.000001")


def _money(value: Any) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 25:
        raise ValueError("Money values must be bounded decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid money amount") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal(100_000):
        raise ValueError("Money is negative, nonfinite or unbounded")
    return parsed


def _validate_rate_card(card: dict[str, Any], model: str) -> tuple[Decimal, Decimal]:
    required = {
        "model_id", "currency", "as_of_date", "source_url",
        "input_usd_per_million_tokens", "output_usd_per_million_tokens",
    }
    if not isinstance(card, dict) or set(card) != required:
        raise ValueError("Frozen pricing snapshot schema mismatch")
    if card["model_id"] != model or card["currency"] != "USD":
        raise ValueError("Model and rate-card currency must match")
    try:
        date.fromisoformat(card["as_of_date"])
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid pricing date") from exc
    url = card["source_url"]
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
        raise ValueError("Source must be a bounded HTTPS URL")
    return _money(card["input_usd_per_million_tokens"]), _money(card["output_usd_per_million_tokens"])


def estimate_synthetic_model_cost(
    report: dict[str, Any], rate_card: dict[str, Any], invoice: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Estimate under a supplied price snapshot, never assert actual charges."""
    _validate(report)
    in_price, out_price = _validate_rate_card(rate_card, report["model_id"])
    prompt = report["provider_prompt_tokens"]
    completion = report["provider_completion_tokens"]
    estimated = (Decimal(prompt) * in_price + Decimal(completion) * out_price) / _MILLION
    claimed_invoice_usd = None
    if invoice is not None:
        required = {"run_id", "model_id", "prompt_tokens", "completion_tokens", "billed_usd"}
        if not isinstance(invoice, dict) or set(invoice) != required:
            raise ValueError("Invoice claim schema mismatch")
        if (
            invoice["run_id"] != report["run_id"]
            or invoice["model_id"] != report["model_id"]
            or type(invoice["prompt_tokens"]) is not int
            or type(invoice["completion_tokens"]) is not int
            or invoice["prompt_tokens"] != prompt
            or invoice["completion_tokens"] != completion
        ):
            raise ValueError("Invoice claim disagrees with frozen run usage")
        claimed_invoice_usd = str(_money(invoice["billed_usd"]).quantize(_QUANTUM))
    digest = hashlib.sha256(
        json.dumps(rate_card, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    # No dollar claims when the rate card is missing; this function requires
    # explicit model-specific pricing and a dated source.
    return {
        "kind": "SELF_DECLARED_SYNTHETIC_COST_ESTIMATE",
        "run_id": report["run_id"],
        "model_id": report["model_id"],
        "data_profile": "SYNTHETIC_DEMO",
        "pricing_as_of_date": rate_card["as_of_date"],
        "pricing_snapshot_sha256": digest,
        "source_url": rate_card["source_url"],
        "usage_source": "OPENAI_COMPATIBLE_REPORTED_USAGE",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "estimated_cost_usd": str(estimated.quantize(_QUANTUM)),
        "declared_invoice_cost_usd": claimed_invoice_usd,
        "estimated_cost_per_passing_case_usd": (
            str((estimated / Decimal(report["passed"])).quantize(_QUANTUM))
            if report["passed"] else None
        ),
        "pricing_independently_verified": False,
        "provider_execution_independently_verified": False,
        "provider_billing_reconciled": False,
        "real_admissions_accuracy": "NOT_MEASURED",
        "production_deployment_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--rate-card", required=True, type=Path)
    parser.add_argument("--invoice-claim", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = estimate_synthetic_model_cost(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.rate_card.read_text(encoding="utf-8")),
        json.loads(args.invoice_claim.read_text(encoding="utf-8")) if args.invoice_claim else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SELF_DECLARED_SYNTHETIC_COST_ESTIMATE (not provider billing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
