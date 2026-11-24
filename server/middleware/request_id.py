"""Request correlation ID middleware.

Every HTTP response carries ``X-Request-ID``. The value is also placed in a
context variable so structured logs and audit rows can be correlated without
threading the ID through every function signature.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from server.observability.agent_trace import continue_trace, safe_agent_span, set_current_span_attribute

logger = logging.getLogger(__name__)

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def get_request_id() -> str:
    return _REQUEST_ID.get() or "-"


def _safe_request_id(value: str | None) -> str:
    if value and _VALID.match(value):
        return value
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """ASGI middleware that assigns/propagates a request correlation ID."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send: Callable[[dict], Awaitable[None]]):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        request_id = _safe_request_id(headers.get("x-request-id"))
        token = _REQUEST_ID.set(request_id)

        async def send_with_header(message: dict) -> None:
            if message.get("type") == "http.response.start":
                set_current_span_attribute("http.status_code", message.get("status"))
                raw_headers = list(message.get("headers", []))
                raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        route = scope.get("path", "")
        group = (
            "agent_admin"
            if route.startswith("/api/v1/admin/agent-")
            else ("public_chat" if route.startswith("/api/v1/chat") else "other")
        )
        method = scope.get("method", "OTHER")
        method = method if method in {"GET", "POST", "DELETE", "PUT", "PATCH"} else "OTHER"
        try:
            with continue_trace(headers.get("traceparent")):
                with safe_agent_span("http.request", {"http.method": method, "http.route_group": group}):
                    await self.app(scope, receive, send_with_header)
        finally:
            _REQUEST_ID.reset(token)
