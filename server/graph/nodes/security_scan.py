"""Security scan node — blocks prompt injection before processing."""

from __future__ import annotations

from typing import Any

from server.middleware.security import detect_injection


def security_scan_node(state: dict[str, Any]) -> dict[str, Any]:
    """Check input for prompt injection patterns.

    If injection is detected, short-circuit the pipeline by setting a
    safe reply, a terminal security flag, and the trace event. The graph
    routes directly to END; rejected input must not alter the profile.
    """
    text = state.get("input_text", "")
    trace = list(state.get("trace", []))

    if detect_injection(text):
        trace.append({"node": "security_scan", "event": "injection_detected"})
        return {
            "reply": "抱歉，您的输入包含特殊指令，无法处理。请重新描述您的需求。",
            "reply_source": "graph",
            "security_blocked": True,
            "trace": trace,
        }

    trace.append({"node": "security_scan", "event": "passed"})
    return {"trace": trace, "security_blocked": False}
