"""Opt-in model-directed Coding Agent actions for fictional scratch repositories.

Not used in CI. The model sees only the fictional task and bounded observations;
the executor retains authority over files, test execution, and final acceptance.
"""

from __future__ import annotations

import json
from typing import Any

from benchmarks.sandbox_coding_agent import CodingAction

_SYSTEM = (
    "You are repairing a fictional Python exercise in an isolated scratch workspace. "
    "Reply with a single JSON object. Allowed actions: "
    '{"kind":"inspect"}, {"kind":"test"}, '
    '{"kind":"edit","content":"complete replacement Python source"}, {"kind":"finish"}. '
    "Never ask to execute a shell command or access a network. "
    "The tool executor, not you, decides when tests passed. "
    "Inspect source first, then edit, test, and finish only after a passing test. "
    "If tests fail, inspect the source and try another edit. "
    "Do not claim real-user or production success."
)


class JSONCodingPlanner:
    """Planner implementation using a caller-supplied OpenAI-compatible client."""

    def __init__(self, client: Any, model: str) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Model name must be explicit")
        self.client = client
        self.model = model

    def decide(self, task: str, observations: list[dict[str, Any]]) -> CodingAction:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=1200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "synthetic_task": task,
                            "bounded_observations": observations[-8:],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        raw = json.loads(response.choices[0].message.content)
        if not isinstance(raw, dict) or raw.get("kind") not in {"inspect", "edit", "test", "finish"}:
            raise ValueError("Model returned an unsupported action")
        if set(raw) - {"kind", "content"}:
            raise ValueError("Model returned unsupported fields")
        content = raw.get("content", "")
        if not isinstance(content, str):
            raise ValueError("Model returned non-string source")
        if raw["kind"] != "edit" and content:
            raise ValueError("Only edit actions may include source")
        usage = getattr(response, "usage", None)
        return CodingAction(
            kind=raw["kind"],
            content=content,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
