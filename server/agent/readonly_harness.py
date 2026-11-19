"""Bounded, read-only Agent Harness experiment for synthetic admissions tasks.

This is a *sidecar experiment*, not the production chat path or an OS sandbox.
Tools are explicitly injected, typed and allowlisted. It never executes shell
commands, sends network requests or persists conversations itself.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from server.agent.synthetic_checkpoint import SQLiteSyntheticCheckpointStore, task_digest

_SOURCE_RE = re.compile(r"^synthetic:[a-z_]+:[0-9a-f]{12}$")
Outcome = Literal["completed", "abstained", "blocked"]


@dataclass(frozen=True)
class Decision:
    """Planner output. Provider usage is optional, never estimated as measured."""

    kind: Literal["tool", "finish", "abstain"]
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ReadOnlyTool:
    """Fixed-schema tool. Every required argument is checked before execution."""

    name: str
    parameters: dict[str, type]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


class Planner(Protocol):
    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        """Select the next read-only tool, finish with evidence, or abstain."""


@dataclass(frozen=True)
class HarnessLimits:
    max_model_steps: int = 8
    max_tool_calls: int = 5
    max_repeated_call: int = 2
    max_observation_chars: int = 800
    max_context_chars: int = 2400
    max_reported_tokens: int = 6000
    require_provider_usage: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.max_model_steps,
            self.max_tool_calls,
            self.max_repeated_call,
            self.max_observation_chars,
            self.max_context_chars,
            self.max_reported_tokens,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Harness limits must be positive integers")


@dataclass
class HarnessResult:
    status: Outcome
    reason: str
    answer: str
    evidence_ids: list[str]
    model_steps: int
    tool_calls: int
    tool_failures: int
    repeated_calls: int
    history_evictions: int
    max_history_chars: int
    prompt_tokens: int | None
    completion_tokens: int | None
    tool_sequence: list[str]
    trace: list[dict[str, Any]]

    def public_report(self) -> dict[str, Any]:
        """Stable allowlist: omit task, free-form answer, args and tool observations."""
        return {
            "status": self.status,
            "reason": self.reason,
            "evidence_count": len(self.evidence_ids),
            "model_steps": self.model_steps,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "repeated_calls": self.repeated_calls,
            "history_evictions": self.history_evictions,
            "max_history_chars": self.max_history_chars,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tool_sequence": list(self.tool_sequence),
            "trace": [dict(item) for item in self.trace],
            "real_admissions_quality": "NOT_MEASURED",
            "os_sandbox": False,
        }


def _valid_parameters(arguments: Any, expected: dict[str, type]) -> bool:
    if not isinstance(arguments, dict) or set(arguments) != set(expected):
        return False
    return all(
        type(arguments[key]) is dtype and (not isinstance(arguments[key], str) or 0 < len(arguments[key]) <= 120)
        for key, dtype in expected.items()
    )


def _usage_value(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def run_readonly_agent(
    task: str,
    planner: Planner,
    tools: dict[str, ReadOnlyTool],
    limits: HarnessLimits | None = None,
    *,
    checkpoint: SQLiteSyntheticCheckpointStore | None = None,
    run_id: str | None = None,
) -> HarnessResult:
    """Execute an agent loop with explicit budget, evidence and repeat gates.

    This controls **between** synchronous calls only. It cannot interrupt a
    blocking tool or model request; use a process/container for hard timeouts.
    """
    limits = limits or HarnessLimits()
    if not isinstance(task, str) or not 0 < len(task) <= 2000:
        raise ValueError("Task must be a non-empty string <= 2000 characters")
    if not tools or set(tools) != {tool.name for tool in tools.values()}:
        raise ValueError("Explicit nonempty tool allowlist with matching names required")
    if any(
        not tool.parameters or any(dtype not in (str, int) for dtype in tool.parameters.values())
        for tool in tools.values()
    ):
        raise ValueError("Only strict string/integer argument schemas are supported")

    schemas = {name: {arg: dtype.__name__ for arg, dtype in tool.parameters.items()} for name, tool in tools.items()}
    history: list[dict[str, Any]] = []
    sources: set[str] = set()
    sequence: list[str] = []
    trace: list[dict[str, Any]] = []
    repeats: Counter[str] = Counter()
    model_steps = tool_calls = tool_failures = repeated_calls = evictions = peak_chars = 0
    prompt_tokens = completion_tokens = 0
    usage_known = True
    answer = ""
    status: Outcome = "blocked"
    reason = "step_budget_exhausted"
    checkpoint_version: int | None = None
    resume_step = 1
    if (checkpoint is None) != (run_id is None):
        raise ValueError("Checkpoint store and synthetic run ID must be supplied together")
    if checkpoint is not None and run_id is not None:
        saved = checkpoint.load(run_id, task_digest(task))
        if saved is not None:
            checkpoint_version, state = saved
            if state.get("phase") == "done":
                return HarnessResult(**state["result"])
            if state.get("phase") != "active":
                raise ValueError("Unknown checkpoint phase")
            history = list(state["history"])
            sources = set(state["sources"])
            sequence = list(state["sequence"])
            trace = list(state["trace"])
            repeats = Counter(state["repeats"])
            model_steps = int(state["model_steps"])
            tool_calls = int(state["tool_calls"])
            tool_failures = int(state["tool_failures"])
            repeated_calls = int(state["repeated_calls"])
            evictions = int(state["evictions"])
            peak_chars = int(state["peak_chars"])
            prompt_tokens = int(state["prompt_tokens"])
            completion_tokens = int(state["completion_tokens"])
            usage_known = bool(state["usage_known"])
            resume_step = model_steps + 1
    for step in range(resume_step, limits.max_model_steps + 1):
        model_steps = step
        try:
            choice = planner.decide(task, [dict(item) for item in history], schemas)
        except Exception:  # noqa: BLE001 - avoid leaking provider error messages
            reason = "planner_failed"
            break
        if not isinstance(choice, Decision):
            reason = "planner_contract_invalid"
            break

        p_tokens = _usage_value(choice.prompt_tokens)
        c_tokens = _usage_value(choice.completion_tokens)
        if p_tokens is None or c_tokens is None:
            usage_known = False
        else:
            prompt_tokens += p_tokens
            completion_tokens += c_tokens
        if not usage_known and limits.require_provider_usage:
            reason = "provider_usage_missing"
            break
        if prompt_tokens + completion_tokens > limits.max_reported_tokens:
            reason = "reported_token_budget_exhausted"
            break

        if choice.kind == "abstain":
            status, reason = "abstained", "explicit_abstention"
            trace.append({"step": step, "kind": "abstain"})
            break

        if choice.kind == "finish":
            requested = choice.evidence_ids
            if (
                not isinstance(choice.answer, str)
                or not 0 < len(choice.answer) <= 2000
                or not isinstance(requested, list)
                or not requested
                or any(not isinstance(ref, str) for ref in requested)
                or not set(requested).issubset(sources)
            ):
                reason = "unverified_finish"
                trace.append({"step": step, "kind": "finish", "result": reason})
                break
            answer = choice.answer
            status, reason = "completed", "evidence_bound_finish"
            trace.append({"step": step, "kind": "finish", "result": "completed"})
            break

        if choice.kind != "tool" or choice.tool not in tools:
            reason = "tool_not_allowlisted"
            trace.append({"step": step, "kind": "tool", "result": reason})
            break
        if tool_calls >= limits.max_tool_calls:
            reason = "tool_budget_exhausted"
            break

        spec = tools[choice.tool]
        if not _valid_parameters(choice.arguments, spec.parameters):
            reason = "tool_schema_invalid"
            trace.append({"step": step, "kind": "tool", "result": reason})
            break
        signature = json.dumps([choice.tool, choice.arguments], sort_keys=True, ensure_ascii=False)
        repeats[signature] += 1
        if repeats[signature] > 1:
            repeated_calls += 1
        if repeats[signature] > limits.max_repeated_call:
            reason = "tool_repeat_loop"
            trace.append({"step": step, "kind": "tool", "result": reason})
            break

        tool_calls += 1
        sequence.append(spec.name)
        start = time.monotonic()
        try:
            result = spec.handler(dict(choice.arguments))
        except Exception:  # noqa: BLE001 - fail closed on all tool errors
            tool_failures += 1
            reason = "tool_execution_failed"
            trace.append({"step": step, "kind": "tool", "tool": spec.name, "result": reason})
            break
        if not isinstance(result, dict) or result.get("data_profile") != "SYNTHETIC_DEMO":
            tool_failures += 1
            reason = "tool_provenance_invalid"
            trace.append({"step": step, "kind": "tool", "tool": spec.name, "result": reason})
            break
        refs = result.get("source_ids")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not _SOURCE_RE.fullmatch(ref) for ref in refs):
            tool_failures += 1
            reason = "tool_evidence_invalid"
            trace.append({"step": step, "kind": "tool", "tool": spec.name, "result": reason})
            break
        sources.update(refs)
        observation = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        history.append(
            {
                "tool": spec.name,
                "observation": observation[: limits.max_observation_chars],
                "source_ids": refs,
            }
        )
        while len(json.dumps(history, ensure_ascii=False)) > limits.max_context_chars and len(history) > 1:
            history.pop(0)
            evictions += 1
        peak_chars = max(peak_chars, len(json.dumps(history, ensure_ascii=False)))
        trace.append(
            {
                "step": step,
                "kind": "tool",
                "tool": spec.name,
                "result": "ok",
                "observed_source_count": len(refs),
                "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
            }
        )
        if checkpoint is not None and run_id is not None:
            snapshot = {
                "data_profile": "SYNTHETIC_DEMO",
                "phase": "active",
                "history": history,
                "sources": sorted(sources),
                "sequence": sequence,
                "trace": trace,
                "repeats": dict(repeats),
                "model_steps": model_steps,
                "tool_calls": tool_calls,
                "tool_failures": tool_failures,
                "repeated_calls": repeated_calls,
                "evictions": evictions,
                "peak_chars": peak_chars,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "usage_known": usage_known,
            }
            checkpoint_version = checkpoint.save(run_id, task_digest(task), checkpoint_version, snapshot)
    else:
        reason = "step_budget_exhausted"

    result = HarnessResult(
        status=status,
        reason=reason,
        answer=answer,
        evidence_ids=sorted(sources),
        model_steps=model_steps,
        tool_calls=tool_calls,
        tool_failures=tool_failures,
        repeated_calls=repeated_calls,
        history_evictions=evictions,
        max_history_chars=peak_chars,
        prompt_tokens=prompt_tokens if usage_known else None,
        completion_tokens=completion_tokens if usage_known else None,
        tool_sequence=sequence,
        trace=trace,
    )
    if checkpoint is not None and run_id is not None and reason != "planner_failed":
        checkpoint.save(
            run_id,
            task_digest(task),
            checkpoint_version,
            {"data_profile": "SYNTHETIC_DEMO", "phase": "done", "result": asdict(result)},
        )
    return result
