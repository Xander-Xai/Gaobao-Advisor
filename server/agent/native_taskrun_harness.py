"""Approved synthetic TaskRun adapter for the project's own model-driven loop."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from server.agent.decision_card_model_planner import DecisionCardModelPlanner
from server.agent.readonly_tools import _require_demo_database
from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card

_MODEL_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")


def run_native_harness(profile: DecisionCardProfile, model: str, *, lease_guard: Callable[[], None]) -> dict[str, Any]:
    """Run bounded model actions with no automatic fallback and no public HTTP spending."""
    if not model.startswith("openai:") or not _MODEL_ID.fullmatch(model.removeprefix("openai:")):
        raise ValueError("Invalid explicit provider model ID")
    requested = model.removeprefix("openai:")
    lease_guard()
    _require_demo_database()
    from openai import OpenAI

    with OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_API_BASE") or None,
        timeout=20.0,
        max_retries=0,
    ) as client:
        identities: list[str] = []

        def guarded_create(**kwargs):
            lease_guard()
            response = client.chat.completions.create(**kwargs)
            actual = getattr(response, "model", None)
            if (
                not isinstance(actual, str)
                or not _MODEL_ID.fullmatch(actual)
                or (identities and actual != identities[0])
            ):
                raise ValueError("Provider model identity missing or changed")
            identities.append(actual)
            lease_guard()
            return response

        guarded = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=guarded_create)))
        planner = DecisionCardModelPlanner(guarded, requested, profile)
        result = build_synthetic_decision_card(
            profile, planner=planner, require_provider_usage=True, lease_guard=lease_guard
        )
        lease_guard()
        execution = result["execution"]
        if (
            not identities
            or execution["model_steps"] != len(identities)
            or execution["prompt_tokens"] is None
            or execution["completion_tokens"] is None
        ):
            raise ValueError("Incomplete provider identity or usage evidence")
        execution["provider_model_identity"] = {
            "requested": requested,
            "reported": identities[0],
            "responses": len(identities),
            "attestation": "PROVIDER_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        }
        result["planner"] = "NATIVE_HARNESS_PROVIDER_DECLARED"
        return result
