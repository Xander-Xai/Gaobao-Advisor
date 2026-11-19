"""Opt-in OpenAI-compatible model planner for the read-only Harness.

Never imported or invoked by offline CI. A caller must explicitly provide a
client and model. Use only fictional SYNTHETIC_DEMO tasks, not user profiles.
"""

from __future__ import annotations

import json
from typing import Any

from server.agent.readonly_harness import Decision

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

    def __init__(self, client: Any, model: str) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("A model name is required")
        self._client = client
        self._model = model

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            max_tokens=384,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
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
