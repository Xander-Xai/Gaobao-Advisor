"""Bounded, provenance-preserving JSON context for the synthetic Agent.

Never slice through serialized JSON or silently strip source IDs. Summarized
observations are marked incomplete; the model must not infer their missing facts.
"""

from __future__ import annotations

import json
from typing import Any


class ContextBudgetExceeded(ValueError):
    """The configured limit cannot preserve safe provenance metadata."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _minimal(refs: list[str]) -> dict[str, Any]:
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "source_ids": list(refs),
        "observation_truncated": True,
    }


def encode_observation(result: dict[str, Any], max_chars: int) -> str:
    """Return intact JSON or an honest provenance-only representation."""
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("Invalid observation character budget")
    if result.get("data_profile") != "SYNTHETIC_DEMO":
        raise ContextBudgetExceeded("Unverified observation profile")
    refs = result.get("source_ids")
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ContextBudgetExceeded("Observation lacks valid provenance list")
    complete = _json(result)
    if len(complete) <= max_chars:
        return complete
    minimal = _json(_minimal(refs))
    if len(minimal) > max_chars:
        raise ContextBudgetExceeded("Source provenance exceeds observation budget")
    return minimal


def bound_history(history: list[dict[str, Any]], max_chars: int) -> tuple[list[dict[str, Any]], int]:
    """Keep newest valid observation history inside its actual JSON size cap."""
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("Invalid context character budget")
    bounded = [dict(item) for item in history]
    evictions = 0
    while len(bounded) > 1 and len(_json(bounded)) > max_chars:
        bounded.pop(0)
        evictions += 1
    if bounded and len(_json(bounded)) > max_chars:
        newest = dict(bounded[-1])
        try:
            observed = json.loads(newest["observation"])
            refs = newest["source_ids"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextBudgetExceeded("Malformed context observation") from exc
        if (
            not isinstance(observed, dict)
            or not isinstance(refs, list)
            or any(not isinstance(ref, str) for ref in refs)
            or observed.get("source_ids") != refs
        ):
            raise ContextBudgetExceeded("Context source IDs do not match observation")
        newest["observation"] = _json(_minimal(refs))
        bounded[-1] = newest
    if len(_json(bounded)) > max_chars:
        raise ContextBudgetExceeded("Context budget cannot retain provenance")
    return bounded, evictions
