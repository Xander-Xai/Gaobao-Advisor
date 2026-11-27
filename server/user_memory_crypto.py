"""At-rest encryption for cross-task user-memory values.

The preference vault is the only memory layer that stores *user-authored*
content. Admission evidence stays in the admissions database; chat summaries
stay in the session snapshot table. This module therefore owns exactly one
concern: turning a preference value into ciphertext that is bound to one
vault and one memory key, and back again under a rotatable keyring.

Design rules:

* AES-GCM with a random 96-bit nonce per write.
* The AAD binds ``vault_id`` and ``memory_key`` so a ciphertext cannot be
  moved to another vault or another key slot.
* Every ciphertext records the id of the key that produced it, so a rotation
  can read old rows with a retired key and rewrite them with the new one
  instead of destroying the history.
* No key material is ever logged, returned or persisted in the database.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

KEY_ENV = "USER_MEMORY_ENCRYPTION_KEY"
PREVIOUS_KEYS_ENV = "USER_MEMORY_ENCRYPTION_PREVIOUS_KEYS"
KEY_FILE_ENV = "USER_MEMORY_ENCRYPTION_KEY_FILE"

ENCRYPTED_PREFIX = "enc:v1:"
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_KEY_ID_LENGTH = 16
_NONCE_LENGTH = 12

# Process-local fallback used when no explicit key is configured outside
# production. It is intentionally ephemeral: a restart invalidates previously
# written ciphertext, which is the honest behaviour for an unset secret.
_EPHEMERAL_KEY: bytes | None = None


class MemoryCryptoError(RuntimeError):
    """Key material is missing or malformed."""


def _parse_key(raw: str, *, source: str) -> bytes:
    raw = raw.strip()
    if not _HEX64.fullmatch(raw):
        raise MemoryCryptoError(f"{source} must contain 64 hex characters")
    return bytes.fromhex(raw)


def key_id(key: bytes) -> str:
    """Stable, non-reversible identifier of a key (safe to store)."""
    return hashlib.sha256(key).hexdigest()[:_KEY_ID_LENGTH]


def _production() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in {"production", "prod"}


def _key_file_path() -> Path:
    return Path(os.getenv(KEY_FILE_ENV, "data/.user_memory_key"))


def _load_or_create_key_file() -> bytes:
    path = _key_file_path()
    try:
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return _parse_key(stored, source=KEY_FILE_ENV)
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_bytes(32).hex()
        path.write_text(generated, encoding="utf-8")
        os.chmod(path, 0o600)
        logger.warning("USER_MEMORY_ENCRYPTION_KEY is unset; generated a local key file at %s", path)
        return bytes.fromhex(generated)
    except MemoryCryptoError:
        raise
    except OSError as exc:
        raise MemoryCryptoError("User-memory key file is not readable") from exc


def active_key() -> bytes:
    """Return the key used for new writes.

    Production requires an explicit environment secret. Development and test
    may fall back to a local key file so that an operator can still exercise
    the encryption path without inventing a fake production posture.
    """
    raw = os.getenv(KEY_ENV, "").strip()
    if raw:
        return _parse_key(raw, source=KEY_ENV)
    if _production():
        raise MemoryCryptoError(f"{KEY_ENV} must be set in production")
    global _EPHEMERAL_KEY
    if _EPHEMERAL_KEY is None:
        try:
            _EPHEMERAL_KEY = _load_or_create_key_file()
        except MemoryCryptoError:
            # Read-only deployments (no data/ write access) still get a stable
            # in-process key; it cannot decrypt rows written by another
            # process, so the caller must treat that as a hard failure.
            _EPHEMERAL_KEY = secrets.token_bytes(32)
            logger.warning("USER_MEMORY_ENCRYPTION_KEY is unset and no key file is writable")
    return _EPHEMERAL_KEY


def keyring() -> dict[str, bytes]:
    """Return ``{key_id: key}`` for the active key plus retired keys.

    Retired keys are read-only. They exist so an operator can rotate the
    active key without destroying already-encrypted preferences.
    """
    ring: dict[str, bytes] = {}
    active = active_key()
    ring[key_id(active)] = active
    raw_previous = os.getenv(PREVIOUS_KEYS_ENV, "")
    for chunk in raw_previous.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        previous = _parse_key(chunk, source=PREVIOUS_KEYS_ENV)
        ring.setdefault(key_id(previous), previous)
    return ring


def aad_for(vault_id: str, memory_key: str) -> bytes:
    """Bind ciphertext to one vault row and one memory key."""
    return f"gaobao-user-memory/v1|{vault_id}|{memory_key}".encode()


def encrypt_value(plaintext: str, *, vault_id: str, memory_key: str, key: bytes | None = None) -> tuple[str, str]:
    """Return ``(stored_value, key_id)`` for one preference value.

    The stored form is ``enc:v1:<key_id>:<base64(nonce||ciphertext||tag)>`` so
    that a rotation can look up the exact key that produced a row.
    """
    if not isinstance(plaintext, str) or not plaintext:
        raise MemoryCryptoError("Preference value must be non-empty text")
    material = key if key is not None else active_key()
    identifier = key_id(material)
    nonce = os.urandom(_NONCE_LENGTH)
    cipher = AESGCM(material)
    sealed = cipher.encrypt(nonce, plaintext.encode("utf-8"), aad_for(vault_id, memory_key))
    stored = ENCRYPTED_PREFIX + identifier + ":" + base64.b64encode(nonce + sealed).decode("ascii")
    return stored, identifier


def decrypt_value(stored: str, *, vault_id: str, memory_key: str, ring: dict[str, bytes] | None = None) -> str | None:
    """Decrypt one stored value, or return ``None`` when it cannot be read.

    A missing key, a tampered ciphertext or a value that was moved to another
    vault all fail closed: the entry is skipped instead of being returned as
    plaintext.
    """
    if not isinstance(stored, str) or not stored.startswith(ENCRYPTED_PREFIX):
        return None
    body = stored[len(ENCRYPTED_PREFIX) :]
    identifier, separator, encoded = body.partition(":")
    if not separator:
        # Older rows may carry a key-less payload; fall back to trying every
        # known key.
        identifier, encoded = "", body
    if ring is None:
        ring = keyring()
    candidates: list[bytes] = []
    if identifier and identifier in ring:
        candidates.append(ring[identifier])
    candidates.extend(material for key, material in ring.items() if material not in candidates)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) <= _NONCE_LENGTH:
        return None
    nonce, sealed = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    for material in candidates:
        try:
            plaintext = AESGCM(material).decrypt(nonce, sealed, aad_for(vault_id, memory_key))
        except (InvalidTag, ValueError):
            continue
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def is_encrypted(stored: str) -> bool:
    """True when the stored form is an AES-GCM envelope."""
    return isinstance(stored, str) and stored.startswith(ENCRYPTED_PREFIX)
