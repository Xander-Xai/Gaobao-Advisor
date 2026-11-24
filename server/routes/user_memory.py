"""Explicit cross-task memory HTTP API; separate from session/profile auth."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from server import user_memory as memory

logger = logging.getLogger(__name__)
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PrivateMemoryRoute(APIRoute):
    """Do not echo invalid preference input or cache credential-related errors."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(
                    status_code=422, detail="Invalid memory request", headers=_PRIVATE_HEADERS
                ) from None
            except HTTPException as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers={**(exc.headers or {}), **_PRIVATE_HEADERS},
                ) from None

        return private_handler


router = APIRouter(prefix="/api/v1/memory", tags=["user-memory"], route_class=PrivateMemoryRoute)


class MemoryWrite(BaseModel):
    value: str = Field(..., strict=True, min_length=1, max_length=120)
    expected_version: int = Field(..., strict=True, ge=0, le=2_147_483_646)
    ttl_days: int = Field(default=180, strict=True, ge=1, le=365)


def _no_store(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except memory.MemoryDisabled as exc:
        raise HTTPException(status_code=503, detail="User memory is not enabled") from exc
    except memory.MemoryUnauthorized as exc:
        raise HTTPException(status_code=401, detail="Invalid memory credential") from exc
    except memory.MemoryConflict as exc:
        raise HTTPException(status_code=409, detail="Memory version conflict") from exc
    except memory.MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory entry not found") from exc
    except memory.MemoryInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("User memory storage failed (error type: %s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="User memory storage unavailable") from exc


def _require_token(token: str | None) -> str:
    # Never accept the ordinary chat session HMAC as a user-memory credential.
    if not token:
        raise HTTPException(status_code=401, detail="Missing memory credential")
    return token


@router.post("/vault", status_code=201)
def create_memory_vault(response: Response):
    """User explicitly creates an independent identity for future tasks."""
    _no_store(response)
    return _call(memory.create_vault)


@router.get("/entries")
def list_memory_entries(response: Response, x_user_memory_token: str | None = Header(None)):
    _no_store(response)
    items = _call(memory.list_memories, _require_token(x_user_memory_token))
    return {"memory_kind": "EXPLICIT_USER", "automatic_capture": False, "items": items, "count": len(items)}


@router.put("/entries/{key}")
def write_memory_entry(key: str, body: MemoryWrite, response: Response, x_user_memory_token: str | None = Header(None)):
    _no_store(response)
    return _call(
        memory.put_memory,
        _require_token(x_user_memory_token),
        key,
        body.value,
        expected_version=body.expected_version,
        ttl_days=body.ttl_days,
    )


@router.delete("/entries/{key}")
def delete_memory_entry(
    key: str,
    response: Response,
    expected_version: int = Query(..., ge=1, le=2_147_483_646),
    x_user_memory_token: str | None = Header(None),
):
    _no_store(response)
    _call(memory.delete_memory, _require_token(x_user_memory_token), key, expected_version=expected_version)
    return {"deleted": True, "key": key}


@router.post("/vault/rotate")
def rotate_memory_credential(response: Response, x_user_memory_token: str | None = Header(None)):
    _no_store(response)
    return _call(memory.rotate_token, _require_token(x_user_memory_token))


@router.delete("/vault")
def erase_memory_vault(response: Response, x_user_memory_token: str | None = Header(None)):
    _no_store(response)
    return _call(memory.erase_vault, _require_token(x_user_memory_token))
