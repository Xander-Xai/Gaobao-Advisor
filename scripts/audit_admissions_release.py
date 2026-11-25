"""Run an offline, read-only admissions release audit.

Use an independently authorized MySQL SELECT-only principal and frozen source files.
Exit 0 only when all supported SQL rows and source hashes match the pinned manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from db.database import engine
from server.services.admissions_lineage import approved_manifest
from server.services.admissions_release_audit import audit_mysql_snapshot
from server.services.industrial_admissions import approved_year


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = audit_mysql_snapshot(engine, approved_manifest(approved_year()), args.sources_dir)
    except Exception as exc:
        # Never print credentials, SQL parameters, source contents or private paths.
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
