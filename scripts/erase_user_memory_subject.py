"""Trusted operator CLI: erase every cross-task vault bound to one subject.

The repository has no account system. The pseudonymous subject id is the
smallest stable per-person identifier available, so erasing a subject is the
account-deletion equivalent for the cross-task memory layer. The credential of
every erased vault stops working immediately.

Example:
    USER_MEMORY_ENABLED=true python -m scripts.erase_user_memory_subject --subject sub_xxx
"""

from __future__ import annotations

import argparse

from server.user_memory import erase_subject


def main() -> int:
    parser = argparse.ArgumentParser(description="Erase all cross-task memory of one subject")
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    result = erase_subject(args.subject)
    print("erased_vaults=" + str(result["erased_vaults"]))
    print("deleted_entries=" + str(result["deleted_entries"]))
    print("backups_and_external_processors_erased=" + str(result["backups_and_external_processors_erased"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
