"""Session HMAC token authentication — lightweight session ownership proof.

No full auth system needed: the client receives a signed token on first
contact and must present it on subsequent requests. Prevents session_id
enumeration attacks.

Production requirement: SESSION_SECRET must be set explicitly.
When running with multiple workers (gunicorn/uvicorn workers > 1),
each worker must share the same SESSION_SECRET or session tokens
will fail verification across workers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger(__name__)

_SECRET: str | None = None


def _get_secret() -> bytes:
    """Return the HMAC signing secret."""
    global _SECRET
    if _SECRET is None:
        _SECRET = os.getenv("SESSION_SECRET", "")
        if not _SECRET:
            _SECRET = _load_or_generate_secret()
    return _SECRET.encode()


def _load_or_generate_secret() -> str:
    """Load secret from file or generate a new one.

    In production (APP_ENV=production), missing SESSION_SECRET is fatal and is
    checked *before* the development file fallback, so a leftover
    ``data/.session_secret`` can never quietly stand in for an explicit
    production secret. In development, the generated secret is persisted to
    ``data/.session_secret`` so restarts and multiple local workers share it.
    """
    env = os.getenv("APP_ENV", "development")
    secret_file = os.path.join("data", ".session_secret")

    # Production: require an explicit SESSION_SECRET, regardless of any file.
    if env == "production":
        raise RuntimeError(
            "SESSION_SECRET must be set in production. Set it via environment variable before starting the server."
        )

    # Try to load existing secret from file
    if os.path.exists(secret_file):
        with open(secret_file, encoding="utf-8") as f:
            secret = f.read().strip()
        if secret:
            logger.info("Loaded session secret from %s", secret_file)
            return secret

    # Development: generate and persist
    secret = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(secret)
        os.chmod(secret_file, 0o600)
        logger.warning(
            "SESSION_SECRET not set — generated ephemeral secret at %s. Set SESSION_SECRET in .env for production.",
            secret_file,
        )
    except OSError as e:
        logger.warning("Could not persist session secret: %s", e)
    return secret


def ensure_secret_ready() -> None:
    """Resolve the session secret at startup so a misconfiguration fails fast.

    In production a missing ``SESSION_SECRET`` raises here (during app
    lifespan) instead of surfacing later as broken session verification on the
    first user request.
    """
    _get_secret()


def create_session_token(session_id: str) -> str:
    """Create an HMAC-SHA256 token for the given session_id."""
    secret = _get_secret()
    return hmac.new(secret, session_id.encode(), hashlib.sha256).hexdigest()


def verify_session_token(session_id: str, token: str | None) -> bool:
    """Verify the token matches the session_id. Returns False on any error."""
    if not session_id or not token:
        return False
    try:
        expected = create_session_token(session_id)
        return hmac.compare_digest(expected, token)
    except Exception:
        return False


def bearer_token(authorization: str | None) -> str | None:
    """Extract the raw token from an ``Authorization: Bearer <token>`` header.

    Returns ``None`` when the header is absent or malformed, so callers can
    distinguish "no credential supplied" from "credential supplied but wrong".
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip() or None


def require_session_auth(session_id: str, authorization: str | None) -> None:
    """Raise HTTP 401 unless the Bearer token proves ownership of ``session_id``.

    Single shared guard used by every endpoint that reads or writes
    session-scoped data (profile, report, feedback, highlight). Reuses the
    existing HMAC session-token mechanism — no separate account system.
    """
    from fastapi import HTTPException

    token = bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing session token")
    if not verify_session_token(session_id, token):
        raise HTTPException(status_code=401, detail="Invalid or expired session token")


def subject_fingerprint(subject: str) -> str:
    """Return a stable, non-reversible id for a pseudonymous subject.

    The subject is a client-supplied identifier that separates one person's
    cross-task memory from another's. It is deliberately *not* a verified
    identity: the fingerprint only proves that the same subject string was
    presented again. It is derived with the server secret so that the raw
    subject never has to be stored.
    """
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Subject must be non-empty text")
    return hmac.new(_get_secret(), subject.strip().encode("utf-8"), hashlib.sha256).hexdigest()
