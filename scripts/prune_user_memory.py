"""Trusted operator CLI: physically prune expired user preferences.

Example:
    USER_MEMORY_ENABLED=true python -m scripts.prune_user_memory --limit 200
"""

from __future__ import annotations

import argparse

from server.user_memory import prune_expired


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove expired cross-task user memory entries")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    result = prune_expired(limit=args.limit)
    print("expired_entries_deleted=" + str(result["expired_entries_deleted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
