"""Opt-in OpenAI-compatible model planner for the read-only Harness.

Never imported or invoked by offline CI. A caller must explicitly provide a
client and model. Use only fictional SYNTHETIC_DEMO tasks, not user profiles.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from server.agent.readonly_harness import Decision
from skills.service import Scene, SkillService

_SYSTEM = (
    "You control a read-only synthetic admissions assistant. "
    "Return exactly one JSON object: "
    "{kind:'tool',tool:'allowlisted_name',arguments:{...}} or "
    "{kind:'finish',facts:[{tool:'school_lookup',school_name:'...',province:'...',"
    "source_id:'...'}],evidence_ids:['source-id']} or "
    "{kind:'abstain'}. Use JSON double quotes. "
    "For finish, include only factual fields observed verbatim in tool results. "
    "Each fact must have tool, source_id and the observed factual fields. "
    "Never invent evidence IDs. Only cite source_ids from prior tool observations. "
    "Do not claim fictional data are real admissions data. "
    "Never request external network, shell, write actions or personal information. "
    "If information is insufficient, abstain."
)


class OpenAIJsonPlanner:
    """Model-driven action selection using the injected OpenAI-style client."""

    def __init__(
        self, client: Any, model: str, *, skill_policy: str = "baseline", skill_service: SkillService | None = None
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("A model name is required")
        self._client = client
        self._model = model
        self._attempted_requests = 0
        self._returned_models: list[str | None] = []
        if skill_policy not in {"baseline", "staged-v1"}:
            raise ValueError("Unsupported skill policy")
        self._skill_policy = skill_policy
        self._skill_contexts: dict[str, str] = {}
        if skill_policy == "staged-v1":
            service = skill_service or SkillService()
            # Server-owned, frozen policy; never select file paths or sections from model text.
            for stage, sections in {
                "initial": ("mental_models",),
                "observed": ("heuristics", "anti_patterns"),
            }.items():
                self._skill_contexts[stage] = service.build_disclosed_context(Scene.GAOKAO, sections, max_chars=5500)

    def skill_policy_receipt(self) -> dict[str, Any]:
        """Report configuration, not measured token savings or model quality."""
        return {
            "policy": self._skill_policy,
            "context_sha256": {
                stage: hashlib.sha256(text.encode("utf-8")).hexdigest() for stage, text in self._skill_contexts.items()
            },
            "context_chars": {stage: len(text) for stage, text in self._skill_contexts.items()},
        }

    def model_identity_receipt(self) -> dict[str, Any]:
        """Provider-declared identity, not independent attestation or billing."""
        models = sorted({model for model in self._returned_models if model is not None})
        consistent = (
            self._attempted_requests > 0
            and len(self._returned_models) == self._attempted_requests
            and all(model is not None for model in self._returned_models)
            and len(models) == 1
        )
        return {
            "requested_model": self._model,
            "provider_reported_model": models[0] if consistent else None,
            "provider_model_identity_consistent": consistent,
            "attempted_requests": self._attempted_requests,
            "returned_responses": len(self._returned_models),
        }

    def _system_context(self, history: list[dict[str, Any]]) -> str:
        if not self._skill_contexts:
            return _SYSTEM
        # Only the execution phase determines disclosure. Observation content
        # cannot request another skill or change the fixed tool allowlist.
        stage = "observed" if history else "initial"
        return _SYSTEM + "\n\n" + self._skill_contexts[stage]

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        self._attempted_requests += 1
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            max_tokens=384,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._system_context(history)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "synthetic_task": task,
                            "allowlisted_readonly_tools": tool_schemas,
                            "recent_tool_results": history,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        returned_model = getattr(response, "model", None)
        self._returned_models.append(
            returned_model if isinstance(returned_model, str) and returned_model.strip() else None
        )
        content = response.choices[0].message.content
        payload = json.loads(content)
        if not isinstance(payload, dict) or payload.get("kind") not in {"tool", "finish", "abstain"}:
            raise ValueError("Planner returned an unsupported JSON action")
        usage = getattr(response, "usage", None)
        args = payload.get("arguments", {})
        refs = payload.get("evidence_ids", [])
        facts = payload.get("facts")
        answer = payload.get("answer", "")
        if payload["kind"] == "finish" and isinstance(facts, list):
            answer = json.dumps({"synthetic_demo": True, "facts": facts}, ensure_ascii=False, sort_keys=True)
        return Decision(
            kind=payload["kind"],
            tool=payload.get("tool", ""),
            arguments=args if isinstance(args, dict) else {},
            answer=answer,
            evidence_ids=refs if isinstance(refs, list) else [],
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
