"""Price cards for task-level cost estimation.

What this module is
-------------------
A *rate card* is a dated, sourced, versioned price for one model.  The
runtime uses it to turn provider-reported token usage into an **estimated**
cost so that a task budget can carry a cost ceiling.

What this module is not
-----------------------
It is **not** a billing system.  A locally computed cost is an estimate
derived from a self-declared price sheet.  It is never presented as a
provider invoice.  ``verified_cost_usd`` is only populated when the provider
itself reports a charged amount, which OpenAI-compatible chat endpoints do
not do; in practice the task-level cost status therefore stays
``ESTIMATED`` (price card available) or ``UNKNOWN`` (no price card).

Schema
------
The rate card keeps the field names already used by
``benchmarks/provider_cost_reconciliation.py`` so an operator can move one
price sheet between the offline reconciliation CLI and the runtime:

``model_id``, ``currency``, ``as_of_date``, ``source_url``,
``input_usd_per_million_tokens``, ``output_usd_per_million_tokens``

and adds two audit fields:

``price_card_version``
    Identifies this revision of the card.
``source``
    Human-readable provenance, e.g. ``provider_pricing_page``.
``verification``
    ``SELF_DECLARED`` (default) or ``PROVIDER_VERIFIED``.  A self-declared
    card may only ever produce an *estimated* cost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

__all__ = ["PriceBook", "PriceCard", "load_price_book"]

_MILLION = Decimal("1000000")
_REQUIRED_FIELDS = {
    "model_id",
    "currency",
    "as_of_date",
    "source_url",
    "input_usd_per_million_tokens",
    "output_usd_per_million_tokens",
}
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"price_card_version", "source", "verification"}


@dataclass(frozen=True)
class PriceCard:
    """A dated, sourced price for one model."""

    model_id: str
    currency: str
    version: str
    source: str
    source_url: str
    as_of: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    verification: str = "SELF_DECLARED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "currency": self.currency,
            "price_card_version": self.version,
            "source": self.source,
            "source_url": self.source_url,
            "as_of_date": self.as_of,
            "verification": self.verification,
            "input_usd_per_million_tokens": str(self.input_price_per_million),
            "output_usd_per_million_tokens": str(self.output_price_per_million),
        }

    def estimate(self, input_tokens: int, output_tokens: int) -> Decimal:
        """Return the locally estimated charge for the given token counts."""
        total = (Decimal(input_tokens) * self.input_price_per_million) / _MILLION + (
            Decimal(output_tokens) * self.output_price_per_million
        ) / _MILLION
        return total.quantize(Decimal("0.00000001"))


class PriceBook:
    """A set of price cards keyed by model id."""

    def __init__(self, cards: dict[str, PriceCard] | None = None) -> None:
        self._cards: dict[str, PriceCard] = dict(cards or {})

    def __len__(self) -> int:
        return len(self._cards)

    def card_for(self, model_id: str | None) -> PriceCard | None:
        if not model_id:
            return None
        if model_id in self._cards:
            return self._cards[model_id]
        # Providers expose the same weights under different deployment names
        # (e.g. "deepseek-chat" vs "deepseek-chat-v3").  Only an explicit
        # alias may bridge two names; nothing is guessed.
        return self._cards.get(f"alias:{model_id}")

    def estimate(
        self, model_id: str | None, input_tokens: int, output_tokens: int
    ) -> tuple[Decimal | None, PriceCard | None]:
        """Return ``(estimated_cost, card)``.

        ``(None, None)`` means the cost of this call is unknown: no price
        card covers the model.  The token ceilings remain authoritative.
        """
        card = self.card_for(model_id)
        if card is None:
            return None, None
        return card.estimate(max(0, input_tokens), max(0, output_tokens)), card

    def to_dict(self) -> dict[str, Any]:
        return {model: card.to_dict() for model, card in sorted(self._cards.items())}


def _parse_decimal(raw: Any, field: str, model_id: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field} in price card for {model_id}") from None
    if value < 0:
        raise ValueError(f"Negative {field} in price card for {model_id}")
    return value


def _parse_card(raw: dict[str, Any]) -> PriceCard:
    if not isinstance(raw, dict):
        raise ValueError("Price card must be an object")
    unknown = set(raw) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unsupported price card fields: {sorted(unknown)}")
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        raise ValueError(f"Missing price card fields: {sorted(missing)}")
    model_id = str(raw["model_id"]).strip()
    if not model_id:
        raise ValueError("Price card model_id must not be empty")
    currency = str(raw["currency"]).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"Price card currency must be a 3-letter code: {model_id}")
    as_of = str(raw["as_of_date"]).strip()
    if len(as_of) != 10 or as_of[4] != "-" or as_of[7] != "-":
        raise ValueError(f"Price card as_of_date must be YYYY-MM-DD: {model_id}")
    verification = str(raw.get("verification") or "SELF_DECLARED").strip().upper()
    if verification not in {"SELF_DECLARED", "PROVIDER_VERIFIED"}:
        raise ValueError(f"Unsupported price card verification: {verification}")
    return PriceCard(
        model_id=model_id,
        currency=currency,
        version=str(raw.get("price_card_version") or "unversioned").strip(),
        source=str(raw.get("source") or "unspecified").strip(),
        source_url=str(raw["source_url"]).strip(),
        as_of=as_of,
        input_price_per_million=_parse_decimal(
            raw["input_usd_per_million_tokens"], "input price", model_id
        ),
        output_price_per_million=_parse_decimal(
            raw["output_usd_per_million_tokens"], "output price", model_id
        ),
        verification=verification,
    )


def _default_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "model_pricing.yaml"


def load_price_book(path: str | os.PathLike[str] | None = None) -> PriceBook:
    """Load the price book.

    The runtime ships ``config/model_pricing.yaml`` with clearly-labelled
    self-declared *test* prices.  Until an operator replaces them with a
    provider-published price sheet, the cost of a real model call is
    ``UNKNOWN`` and the cost ceiling cannot be enforced for that model.

    ``GAOBAO_TASK_BUDGET_PRICE_BOOK`` points at an operator-managed price
    sheet without requiring a code change.
    """
    if path is None:
        configured = os.getenv("GAOBAO_TASK_BUDGET_PRICE_BOOK", "").strip()
        target = Path(configured) if configured else _default_path()
    else:
        target = Path(path)
    if not target.exists():
        return PriceBook()
    raw_text = target.read_text(encoding="utf-8")
    if target.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        document = yaml.safe_load(raw_text) or {}
    else:
        document = json.loads(raw_text)
    if not isinstance(document, dict):
        raise ValueError("Price book must be a mapping")
    entries = document.get("cards")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError("Price book 'cards' must be a list")
    cards: dict[str, PriceCard] = {}
    for entry in entries:
        card = _parse_card(entry)
        if card.model_id in cards:
            raise ValueError(f"Duplicate price card for {card.model_id}")
        cards[card.model_id] = card
    return PriceBook(cards)
