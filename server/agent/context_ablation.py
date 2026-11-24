"""Decision-card Context A/B: preserve provenance while minimizing model-visible history.

This is an OPT-IN synthetic-only planning-prompt adapter. It does not change
persistent executor history, production chat, tool permissions, or the SQL
Evidence Gate. Compact context contains only facts needed to choose a next tool,
abstain, or submit the already-observed source identities. Admission numbers
remain in SQL for independent final validation, NOT in model-authored output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from server.agent.decision_card_model_planner import DecisionCardModelPlanner
from server.agent.readonly_harness import Decision

ContextArm = Literal["full", "evidence_compact"]
_SOURCE_RE = re.compile(r"^synthetic:(major|admission|plan):[0-9a-f]{12}$")
_FIELDS = {
    "major_lookup": frozenset({"data_profile", "record_count", "source_ids", "major_name", "category"}),
    "admission_lookup": frozenset(
        {
            "data_profile",
            "record_count",
            "source_ids",
            "school_name",
            "major_name",
            "province",
            "year",
            "subject_type",
            "min_score",
            "min_rank",
            "batch",
        }
    ),
    "plan_lookup": frozenset(
        {
            "data_profile",
            "record_count",
            "source_ids",
            "school_name",
            "major_name",
            "province",
            "year",
            "subject_requirement",
            "plan_count",
            "batch",
        }
    ),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def compress_history(history: list[dict[str, Any]], arm: ContextArm) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Project tool observations using a fixed allowlist, never an LLM summary.

    Full retains the original observations; compact strips redundant SQL fields
    and includes explicit validity flags. Every source ID, record count, entity
    identity and subject constraint must survive unchanged. No truncation.
    """
    if arm not in {"full", "evidence_compact"} or not isinstance(history, list) or len(history) > 5:
        raise ValueError("Unsupported synthetic context arm or history length")
    prepared: list[dict[str, Any]] = []
    full_chars = sent_chars = refs_preserved = compacted = 0
    seen: set[tuple[str, str]] = set()
    for event in history:
        if not isinstance(event, dict) or set(event) != {"tool", "observation", "source_ids"}:
            raise ValueError("Tool history must follow the existing exact contract")
        tool = event["tool"]
        if tool not in _FIELDS or not isinstance(event["observation"], str):
            raise ValueError("Unrecognized tool or malformed observation")
        try:
            observation = json.loads(event["observation"])
        except (ValueError, TypeError) as exc:
            raise ValueError("Malformed synthetic JSON observation") from exc
        if not isinstance(observation, dict) or observation.get("data_profile") != "SYNTHETIC_DEMO":
            raise ValueError("Non-synthetic or malformed observation")
        if set(observation) - _FIELDS[tool]:
            raise ValueError("Unexpected business fields: cannot silently compact schema drift")
        refs = observation.get("source_ids")
        count = observation.get("record_count")
        if (
            type(count) is not int
            or count not in {0, 1}
            or not isinstance(refs, list)
            or (count == 0 and refs != [])
            or (count == 1 and len(refs) != 1)
            or any(
                not isinstance(ref, str)
                or not _SOURCE_RE.fullmatch(ref)
                or not ref.startswith(f"synthetic:{tool.removesuffix('_lookup')}:")
                for ref in refs
            )
            or refs != event["source_ids"]
        ):
            raise ValueError("Invalid or mismatched source provenance")
        identity_field = "major_name" if tool == "major_lookup" else "school_name"
        identity = observation.get(identity_field)
        if not isinstance(identity, str) or "（合成）" not in identity:
            raise ValueError("Unverified synthetic entity identity")
        key = (tool, identity)
        if key in seen:
            raise ValueError("Duplicated tool identity")
        seen.add(key)
        refs_preserved += len(refs)
        raw_event = {"tool": tool, "observation": event["observation"], "source_ids": list(refs)}
        if arm == "full":
            chosen = raw_event
        else:
            minimal: dict[str, Any] = {
                "data_profile": "SYNTHETIC_DEMO",
                "record_count": count,
                "source_ids": list(refs),
                identity_field: identity,
            }
            if tool == "major_lookup":
                if "category" in observation:
                    minimal["category"] = observation["category"]
            elif tool == "admission_lookup":
                if "subject_type" in observation:
                    minimal["subject_type"] = observation["subject_type"]
                if count == 1:
                    minimal["valid_score_rank"] = (
                        type(observation.get("min_score")) is int
                        and type(observation.get("min_rank")) is int
                        and observation["min_rank"] > 0
                    )
            else:
                if "subject_requirement" in observation:
                    minimal["subject_requirement"] = observation["subject_requirement"]
                if count == 1:
                    minimal["valid_plan_count"] = (
                        type(observation.get("plan_count")) is int and observation["plan_count"] > 0
                    )
            proposal = {"tool": tool, "observation": _json(minimal), "source_ids": list(refs)}
            # Compact is not allowed to increase the model's history payload.
            chosen = proposal if len(_json(proposal)) < len(_json(raw_event)) else raw_event
            compacted += int(chosen is proposal)
        full_chars += len(_json(raw_event))
        sent_chars += len(_json(chosen))
        prepared.append(chosen)
        projected = json.loads(chosen["observation"])
        if (
            projected["source_ids"] != refs
            or projected["record_count"] != count
            or projected[identity_field] != identity
            or any(
                field in observation and projected.get(field) != observation[field]
                for field in ("subject_type", "subject_requirement", "category")
            )
        ):
            raise ValueError("Context projection changed a decision-critical fact")
    return prepared, {
        "full_observation_chars": full_chars,
        "model_visible_observation_chars": sent_chars,
        "source_ids_preserved": refs_preserved,
        "observations_compacted": compacted,
    }


@dataclass
class ContextABPlanner:
    """Wrap the exact original model planner; only its history representation changes."""

    inner: DecisionCardModelPlanner
    arm: ContextArm
    context_metrics: list[dict[str, int]] = field(default_factory=list)

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        projected, metrics = compress_history(history, self.arm)
        self.context_metrics.append(metrics)
        return self.inner.decide(task, projected, tool_schemas)

    def public_context_stats(self) -> dict[str, int]:
        return {
            "history_full_chars": sum(item["full_observation_chars"] for item in self.context_metrics),
            "history_sent_chars": sum(item["model_visible_observation_chars"] for item in self.context_metrics),
            "source_refs_preserved_across_steps": sum(item["source_ids_preserved"] for item in self.context_metrics),
            "compacted_observations_across_steps": sum(item["observations_compacted"] for item in self.context_metrics),
            "decision_calls": len(self.context_metrics),
        }
