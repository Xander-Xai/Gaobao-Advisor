"""Structured administrator API errors.

All admin endpoints return the same envelope::

    {"code": "JOB_NOT_FOUND", "message": "...", "request_id": "...", "details": {...}}

Non-admin endpoints keep FastAPI's historical ``{"detail": ...}`` shape.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.middleware.request_id import get_request_id

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "/api/v1/admin"


class AdminError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def is_admin_path(path: str) -> bool:
    return path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")


def _envelope(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": get_request_id(),
    }
    if details:
        body["details"] = details
    return body


def _http_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "ADMIN_TOKEN_INVALID",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        429: "RATE_LIMITED",
        503: "ADMIN_NOT_CONFIGURED",
    }.get(status_code, f"HTTP_{status_code}")


def register_admin_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AdminError)
    async def _admin_error_handler(request: Request, exc: AdminError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(
                "admin request failed code=%s path=%s request_id=%s",
                exc.code,
                request.url.path,
                get_request_id(),
            )
        headers = {"X-Request-ID": get_request_id(), **exc.headers}
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, details=exc.details),
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if is_admin_path(request.url.path):
            return JSONResponse(
                status_code=exc.status_code,
                content=_envelope(_http_code(exc.status_code), str(exc.detail)),
                headers={"X-Request-ID": get_request_id(), **(exc.headers or {})},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if is_admin_path(request.url.path):
            errors = []
            for item in exc.errors():
                errors.append(
                    {
                        "loc": [str(part) for part in item.get("loc", [])],
                        "msg": item.get("msg", "invalid value"),
                        "type": item.get("type", "value_error"),
                    }
                )
            return JSONResponse(
                status_code=422,
                content=_envelope("VALIDATION_ERROR", "请求参数校验失败", details={"errors": errors}),
                headers={"X-Request-ID": get_request_id()},
            )
        return JSONResponse(
            status_code=422, content={"detail": exc.errors()}, headers={"X-Request-ID": get_request_id()}
        )


class AdminExceptionMiddleware:
    """Convert unexpected errors under ``/api/v1/admin`` into the JSON envelope.

    Exceptions handled by FastAPI's own exception middleware never reach this
    layer, so 401/403/404/409/422 keep their specific codes. This only catches
    genuine 500-class programming/database failures and logs them once with the
    request correlation ID.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not is_admin_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception:
            logger.exception("unhandled admin exception path=%s request_id=%s", scope.get("path"), get_request_id())
            response = JSONResponse(
                status_code=500,
                content=_envelope("INTERNAL_ERROR", "服务内部错误，请稍后重试或联系管理员"),
                headers={"X-Request-ID": get_request_id()},
            )
            await response(scope, receive, send)
