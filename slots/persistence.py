"""Canonical conversion between stored slot records and flat graph slots.

Two formats exist in this project for historical reasons:

* the graph state uses flat values (``{"province": "河北", "score": 600}``);
* ``Conversation.slots_json`` stores slot records used by the profile and
  onboarding endpoints (``{"province": {"value": "河北", "filled": True}}``).

Writing mixed formats into one column made ``GET /profile`` return an empty
profile after a chat turn. These helpers are the single boundary: storage is
canonical slot records, while the graph always sees flat values.
"""

from __future__ import annotations

import re
from typing import Any

# Keys owned by the profile. ``score`` is deliberately derived from
# ``score_rank`` and is not persisted as a separate slot record.
PROFILE_SLOT_KEYS: frozenset[str] = frozenset(
    {
        "province",
        "score",
        "score_rank",
        "subject",
        "interest",
        "region",
        "family",
        "goal",
    }
)

# Metadata keys live in the same JSON column but are not profile slots.
_METADATA_KEYS: frozenset[str] = frozenset({"_query_state"})

_SCORE_RE = re.compile(r"(\d{2,3})")


def _is_slot_record(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value


def _numeric_score_from_rank(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        score = int(value)
        return score if 100 <= score <= 750 else None
    text = str(value).strip()
    if not text or "分" not in text:
        return None
    match = _SCORE_RE.search(text)
    if not match:
        return None
    score = int(match.group(1))
    return score if 100 <= score <= 750 else None


def _coerce_score(value: Any) -> int | None:
    """Accept a numeric score as int/float, bare digits, or "620分"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        score = int(value)
        return score if 100 <= score <= 750 else None
    text = str(value).strip()
    if text.isdigit():
        score = int(text)
        return score if 100 <= score <= 750 else None
    return _numeric_score_from_rank(text)


def _record_value(value: Any) -> Any:
    """Return a usable value from either a slot record or a flat value."""
    if _is_slot_record(value):
        if not value.get("filled"):
            return None
        raw = value.get("value")
    else:
        raw = value
    if raw is None or raw == "" or raw is False:
        return None
    return raw


def flatten_slots(stored: dict[str, Any] | None, *, derive_score: bool = True) -> dict[str, Any]:
    """Convert stored slot records (or legacy flat values) into graph slots.

    Metadata such as ``_query_state`` is intentionally excluded; chat callers
    load it separately so it is not mistaken for a user profile field.
    """
    flat: dict[str, Any] = {}
    if not isinstance(stored, dict):
        return flat
    for key, value in stored.items():
        if key in _METADATA_KEYS or key.startswith("_"):
            continue
        raw = _record_value(value)
        if raw is not None:
            flat[key] = raw

    if not derive_score:
        return flat

    # A stored score_rank is the user-facing source of truth. Re-derive the
    # numeric score so a corrected score cannot keep a stale sibling value.
    score = _numeric_score_from_rank(flat.get("score_rank"))
    if score is not None:
        flat["score"] = score
    elif flat.get("score") is not None:
        score = _coerce_score(flat.get("score"))
        if score is not None:
            flat["score"] = score
    return flat


def normalize_slots(
    flat: dict[str, Any] | None,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build canonical slot records from graph slots.

    Existing records are preserved for fields the graph did not touch, so a
    chat turn cannot drop an optional profile field. ``score`` is persisted as
    ``score_rank`` so the profile endpoints and the chat path agree.
    """
    result: dict[str, Any] = {}
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key in _METADATA_KEYS or key.startswith("_"):
                continue
            result[key] = value

    incoming = flat or {}
    for key, value in incoming.items():
        if key in _METADATA_KEYS or key.startswith("_"):
            continue
        if key in PROFILE_SLOT_KEYS:
            continue
        if value is not None and value != "" and value is not False:
            result[key] = value

    for key in PROFILE_SLOT_KEYS - {"score"}:
        value = _record_value(incoming.get(key))
        if value is not None:
            result[key] = {"value": value, "filled": True}

    # Accept a numeric score even when the caller did not provide score_rank.
    # Missing incoming values preserve the existing record; clearing a profile
    # field is the responsibility of ``save_profile``.
    if _record_value(incoming.get("score_rank")) is None:
        score = _coerce_score(incoming.get("score"))
        if score is not None:
            result["score_rank"] = {"value": f"{score}分", "filled": True}
    result.pop("score", None)

    query_state = incoming.get("_query_state")
    if not query_state and isinstance(existing, dict):
        query_state = existing.get("_query_state")
    if query_state:
        result["_query_state"] = query_state
    return result


def extract_query_state(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return the persisted ``_query_state`` mapping, or an empty dict."""
    if isinstance(stored, dict) and isinstance(stored.get("_query_state"), dict):
        return dict(stored["_query_state"])
    return {}
