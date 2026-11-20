"""Independent structural checks for redacted SYNTHETIC_DEMO Agent step traces.

This verifies what the executor *recorded*, not external model identity,
provider invoices, complete internal reasoning, expert admissions truth or
the correctness of model-provided instructions. Tool payloads, arguments,
prompts, source IDs and exception text are rejected from exported receipts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

TRACE_CONTRACT = "SYNTHETIC_AGENT_STEP_TRACE_V1"
_TOOLS = frozenset({"major_lookup", "admission_lookup", "plan_lookup"})
_KINDS = frozenset({"tool", "finish", "abstain", "blocked", "planner_error"})
_FIELDS = frozenset(
    {
        "step",
        "kind",
        "tool",
        "result",
        "observed_source_count",
        "elapsed_ms",
        "model_elapsed_ms",
        "prompt_tokens",
        "completion_tokens",
    }
)
_EXECUTED = frozenset(
    {"ok", "tool_execution_failed", "tool_provenance_invalid", "tool_evidence_invalid", "context_budget_exhausted"}
)
_TOOL_ERRORS = frozenset({"tool_not_allowlisted", "tool_schema_invalid", "tool_repeat_loop", *_EXECUTED})
_BLOCKED = frozenset({"provider_usage_missing", "reported_token_budget_exhausted", "tool_budget_exhausted"})
_TERMINAL = frozenset({"planner_failed", "planner_contract_invalid"})


def _bounded_number(value: Any, limit: float) -> bool:
    return type(value) in (int, float) and 0 <= value <= limit


def _bounded_tokens(value: Any) -> bool:
    return value is None or (type(value) is int and 0 <= value <= 1_000_000)


def grade_step_trace(execution: dict[str, Any]) -> dict[str, Any]:
    """Return a safe trace receipt or raise ValueError (fail closed).

    Step means one planner decision; tool_calls means actually invoked handlers.
    Successful decisions, abstentions and blocked steps are all represented.
    """
    if not isinstance(execution, dict) or execution.get("os_sandbox") is not False:
        raise ValueError("Expected explicitly non-sandboxed synthetic harness report")
    events = execution.get("trace")
    steps = execution.get("model_steps")
    tool_calls = execution.get("tool_calls")
    if (
        not isinstance(events, list)
        or type(steps) is not int
        or not 1 <= steps <= 8
        or len(events) != steps
        or type(tool_calls) is not int
        or not 0 <= tool_calls <= 5
    ):
        raise ValueError("Incomplete or unbounded Agent decision trace")
    output: list[dict[str, Any]] = []
    invoked: list[str] = []
    known_tokens = True
    prompt_total = completion_total = 0
    for step, event in enumerate(events, 1):
        if not isinstance(event, dict) or set(event) - _FIELDS:
            raise ValueError("Unexpected trace fields (possibly raw model data)")
        if event.get("step") != step or type(event.get("step")) is not int:
            raise ValueError("Non-monotonic or duplicated model decision step")
        kind, result = event.get("kind"), event.get("result")
        if kind not in _KINDS or not isinstance(result, str):
            raise ValueError("Unknown decision trace action")
        if not _bounded_number(event.get("model_elapsed_ms"), 1_000_000):
            raise ValueError("Missing bounded model decision latency")
        p, c = event.get("prompt_tokens"), event.get("completion_tokens")
        if not _bounded_tokens(p) or not _bounded_tokens(c) or (p is None) != (c is None):
            raise ValueError("Invalid or partially missing per-step provider usage")
        if p is None:
            known_tokens = False
        else:
            prompt_total += p
            completion_total += c
        if kind == "tool":
            if result not in _TOOL_ERRORS:
                raise ValueError("Unknown tool outcome")
            name = event.get("tool")
            if result in _EXECUTED:
                if name not in _TOOLS:
                    raise ValueError("Invoked tool is not in synthetic allowlist")
                invoked.append(name)
            elif name is not None:
                raise ValueError("Pre-execution failure must not claim a tool invocation")
            if result == "ok":
                sources = event.get("observed_source_count")
                if type(sources) is not int or not 0 <= sources <= 5:
                    raise ValueError("Successful tool must report bounded evidence count")
                if not _bounded_number(event.get("elapsed_ms"), 1_000_000):
                    raise ValueError("Successful tool must report tool latency")
            elif "observed_source_count" in event or "elapsed_ms" in event:
                raise ValueError("Failed or rejected tool cannot claim a successful observation")
        else:
            if "tool" in event or "elapsed_ms" in event or "observed_source_count" in event:
                raise ValueError("Non-tool decision cannot claim tool execution")
            if kind == "finish" and result not in {"completed", "unverified_finish"}:
                raise ValueError("Invalid finish result")
            if kind == "abstain" and result != "explicit_abstention":
                raise ValueError("Invalid abstain result")
            if kind == "blocked" and result not in _BLOCKED:
                raise ValueError("Invalid budget/usage denial")
            if kind == "planner_error" and result not in _TERMINAL:
                raise ValueError("Invalid redacted planner error")
        if step < len(events) and (kind != "tool" or result != "ok"):
            raise ValueError("Terminal decision followed by further execution")
        output.append(dict(event))
    if invoked != execution.get("tool_sequence") or len(invoked) != tool_calls:
        raise ValueError("Recorded tool invocations disagree with execution counters")
    expected_prompt = prompt_total if known_tokens else None
    expected_completion = completion_total if known_tokens else None
    if execution.get("prompt_tokens") != expected_prompt or execution.get("completion_tokens") != expected_completion:
        raise ValueError("Per-step provider usage disagrees with run totals")
    status, reason = execution.get("status"), execution.get("reason")
    last = events[-1]
    if (
        (
            last["kind"] == "finish"
            and (status, reason)
            != (
                ("completed", "evidence_bound_finish")
                if last["result"] == "completed"
                else ("blocked", "unverified_finish")
            )
        )
        or (last["kind"] == "abstain" and (status, reason) != ("abstained", "explicit_abstention"))
        or (last["kind"] in {"blocked", "planner_error"} and (status, reason) != ("blocked", last["result"]))
        or (last["kind"] == "tool" and last["result"] != "ok" and (status, reason) != ("blocked", last["result"]))
        or (
            last["kind"] == "tool"
            and last["result"] == "ok"
            and (status != "blocked" or reason != "step_budget_exhausted")
        )
    ):
        raise ValueError("Final result is inconsistent with terminal decision")
    canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "trace_contract": TRACE_CONTRACT,
        "step_events": output,
        "trace_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "model_decisions": steps,
        "executed_tool_calls": tool_calls,
        "provider_prompt_tokens": expected_prompt,
        "provider_completion_tokens": expected_completion,
        "validated_redacted_trace": True,
        "provider_origin_independently_attested": False,
        "complete_model_internal_reasoning": False,
        "real_admissions_accuracy": "NOT_MEASURED",
    }


def check_trace_receipt(row: dict[str, Any]) -> None:
    """Regrade the exported receipt; report SHA is an integrity check, not a signature."""
    receipt = row.get("trace_receipt")
    if not isinstance(receipt, dict) or receipt.get("trace_contract") != TRACE_CONTRACT:
        raise ValueError("Missing versioned redacted trace receipt")
    if receipt.get("validated_redacted_trace") is not True:
        raise ValueError("Trace was not validated")
    if not isinstance(receipt.get("step_events"), list):
        raise ValueError("Malformed or missing redacted step events")
    expected = grade_step_trace(
        {
            "trace": receipt.get("step_events"),
            "model_steps": row.get("model_steps"),
            "tool_calls": row.get("tool_calls"),
            "tool_sequence": [
                event["tool"]
                for event in receipt.get("step_events", [])
                if isinstance(event, dict) and event.get("kind") == "tool" and event.get("result") in _EXECUTED
            ],
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "status": row.get("execution_status"),
            "reason": row.get("execution_reason"),
            "os_sandbox": False,
        }
    )
    if receipt != expected:
        raise ValueError("Redacted trace receipt content/digest/counters have been altered")


def trace_failure_category(row: dict[str, Any]) -> str | None:
    """Return only a bounded failure reason for development-set diagnosis."""
    check_trace_receipt(row)
    if row.get("execution_status") != "blocked":
        return None
    reason = row.get("execution_reason")
    return (
        reason
        if reason in _TOOL_ERRORS | _BLOCKED | _TERMINAL | {"unverified_finish", "step_budget_exhausted"}
        else None
    )
