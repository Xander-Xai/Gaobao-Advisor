"""Profile check node — identifies missing required fields."""

from __future__ import annotations

from typing import Any

from server.graph.nodes.route import SCENE_CONFIGS


def profile_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """Check whether all required slots for the scene are filled.

    Sets missing_fields to any required slot that is missing or falsy.
    Also builds a profile_snapshot from filled slots.
    """
    scene = state.get("scene", "general")
    slots = state.get("slots", {})
    config = SCENE_CONFIGS.get(scene, SCENE_CONFIGS["general"])
    from server.industrial_runtime import industrial_enabled

    required = config["required_slots"]
    if industrial_enabled() and scene == "gaokao":
        required = ["province", "score_rank", "subject"]

    missing = [field for field in required if not slots.get(field)]

    # Build profile snapshot from filled slots
    profile = {k: v for k, v in slots.items() if v}

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "profile_check",
            "event": f"missing={len(missing)}",
            "missing_fields": missing,
        }
    )

    result: dict[str, Any] = {
        "missing_fields": missing,
        "profile_snapshot": profile,
        "trace": trace,
    }
    return result
