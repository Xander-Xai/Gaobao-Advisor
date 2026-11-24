"""Opt-in, reversible prompt canary for *synthetic* Agent model experiments.

The existing admin-attested optimizer pointer does not affect public /chat.
This selector only changes the explicitly authorized offline model-eval CLI.
Assignment is deterministic and never accepts a user profile as the hash key.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

_KEY = re.compile(r"^synthetic-canary-[a-z0-9_-]{8,64}$")


def choose_synthetic_variant(registry: Any, run_key: str, percentage: int = 10) -> dict[str, Any]:
    """Deterministic per-run rollout; administrative rollback returns baseline."""
    if not isinstance(run_key, str) or not _KEY.fullmatch(run_key):
        raise ValueError("Only opaque synthetic canary execution keys are allowed")
    if type(percentage) is not int or not 0 <= percentage <= 100:
        raise ValueError("Canary allocation percentage must be 0..100")
    if os.getenv("APP_ENV", "").strip().lower() not in {"development", "test", "local"}:
        raise ValueError("Synthetic canary requires explicit non-production APP_ENV")
    if os.getenv("ENABLE_OUTBOUND_FETCH", "false").lower() != "false":
        raise ValueError("Disable arbitrary outbound fetching in synthetic canary mode")
    enabled = os.getenv("ENABLE_SYNTHETIC_AGENT_CANARY", "false").lower() == "true"
    if not enabled:
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "prompt_variant": "baseline",
            "allocation": "DISABLED",
            "production_canary": False,
            "real_admissions_quality": "NOT_MEASURED",
        }
    pointer = registry.demo_snapshot()
    proposal_id = pointer.get("active_demo_pointer")
    if proposal_id is None:
        return {
            "data_profile": "SYNTHETIC_DEMO",
            "prompt_variant": "baseline",
            "allocation": "ROLLED_BACK_OR_UNSTAGED",
            "production_canary": False,
            "real_admissions_quality": "NOT_MEASURED",
        }
    if not isinstance(proposal_id, str) or not re.fullmatch(r"opt_[0-9a-f]{32}", proposal_id):
        raise ValueError("Invalid synthetic canary stage pointer")
    proposal = registry.get(proposal_id)
    if (
        proposal.get("status") != "DEMO_POINTER_ACTIVE"
        or proposal.get("report", {}).get("evaluation_gate") != "REVIEW_REQUIRED"
        or len(proposal.get("signoffs", [])) != 2
        or any(item.get("verdict") != "attest_reviewed" for item in proposal["signoffs"])
        or any(not isinstance(item.get("reviewer"), str) or not item["reviewer"].strip() for item in proposal["signoffs"])
        or len({item["reviewer"] for item in proposal["signoffs"]}) != 2
    ):
        raise ValueError("Canary pointer has no eligible frozen, reviewed synthetic candidate")
    bucket = int.from_bytes(hashlib.sha256((proposal_id + ":" + run_key).encode()).digest()[:4], "big") % 100
    allocated = bucket < percentage
    return {
        "data_profile": "SYNTHETIC_DEMO",
        "prompt_variant": "evidence_first" if allocated else "baseline",
        "allocation": "SYNTHETIC_CANDIDATE" if allocated else "SYNTHETIC_CONTROL",
        "proposal_id": proposal_id,
        "allocation_bucket": bucket,
        "configured_rollout_percent": percentage,
        "production_canary": False,
        "source_report_independently_verified": False,
        "real_admissions_quality": "NOT_MEASURED",
    }
