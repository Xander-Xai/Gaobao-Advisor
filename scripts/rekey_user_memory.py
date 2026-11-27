"""Trusted operator CLI: re-encrypt legacy plaintext preferences.

Rows written before the vault encryption migration keep their plaintext form.
This tool rewrites them under the active key without changing the value, the
version or the expiry.

Example:
    USER_MEMORY_ENABLED=true python -m scripts.rekey_user_memory --limit 200
"""

from __future__ import annotations

import argparse

from db.database import get_session
from db.models import UserMemoryEntry
from server.user_memory import enabled
from server.user_memory_crypto import encrypt_value


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt legacy plaintext user-memory rows")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if not enabled():
        print("user memory is disabled; nothing to re-key")
        return 1
    if not 1 <= args.limit <= 1000:
        print("limit must be 1..1000")
        return 2
    db = get_session()
    try:
        rows = db.query(UserMemoryEntry).filter(~UserMemoryEntry.value.startswith("enc:v1:")).limit(args.limit).all()
        rewritten = 0
        for row in rows:
            stored, key_id = encrypt_value(row.value, vault_id=row.vault_id, memory_key=row.memory_key)
            row.value = stored
            row.key_id = key_id
            rewritten += 1
        db.commit()
        print("legacy_rows_rewritten=" + str(rewritten))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
