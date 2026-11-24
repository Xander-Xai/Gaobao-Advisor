"""Operator-owned sandbox tool for analysis of authorized admissions SQL data.

The caller supplies the model-generated Python program; it never runs on the
host. The database credential and source authorization stay in the host.
This is a single-operator local tool, not an anonymous code-execution API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import statistics
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from server.agent.sandbox_runtime import SandboxOutputExceeded, _bounded_docker_run, _remove_and_verify_container

MAX_ROWS = 2000
MAX_GROUPS = 200
MAX_CODE_BYTES = 16 * 1024
GROUP_FIELDS = {"year", "school_id", "major_id"}
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
FIELDS = ("id", "school_id", "major_id", "year", "min_score", "max_score", "min_rank")


@dataclass(frozen=True)
class Scope:
    province: str
    subject_type: str
    batch: str
    year_from: int
    year_to: int
    level: str = "school"
    school_id: int | None = None

    def __post_init__(self):
        if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= limit for x, limit in (
            (self.province, 20), (self.subject_type, 10), (self.batch, 20)
        )):
            raise ValueError("Invalid admissions cohort")
        if type(self.year_from) is not int or type(self.year_to) is not int:
            raise ValueError("Invalid year range")
        if not 2000 <= self.year_from <= self.year_to <= 2100 or self.year_to - self.year_from > 9:
            raise ValueError("Limit the analysis to at most ten admission years")
        if self.level not in {"school", "major"}:
            raise ValueError("Specify school-level or major-level records")
        if self.school_id is not None and (type(self.school_id) is not int or self.school_id < 1):
            raise ValueError("Invalid school ID")


def collect_admissions_rows(session: Any, scope: Scope) -> list[dict[str, Any]]:
    """Use the actual admissions table, never the conversation or account tables."""
    from sqlalchemy import select

    from db.models import AdmissionScore

    model = AdmissionScore
    query = select(*(getattr(model, field) for field in FIELDS)).where(
        model.province == scope.province,
        model.subject_type == scope.subject_type,
        model.batch == scope.batch,
        model.year.between(scope.year_from, scope.year_to),
        model.major_id.is_(None) if scope.level == "school" else model.major_id.is_not(None),
    )
    if scope.school_id is not None:
        query = query.where(model.school_id == scope.school_id)
    rows = [
        dict(row)
        for row in session.execute(query.order_by(model.id).limit(MAX_ROWS + 1)).mappings()
    ]
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("Empty or oversized cohort: narrow the query, never silently truncate")
    return rows


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _expected(rows: list[dict[str, Any]], group_by: tuple[str, ...]) -> dict[str, Any]:
    """Independent host oracle for supported descriptive statistics, not admission predictions."""
    if not 1 <= len(group_by) <= 2 or len(set(group_by)) != len(group_by) or not set(group_by) <= GROUP_FIELDS:
        raise ValueError("Unsupported group fields")
    if not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError("Invalid cohort size")
    groups: dict[tuple[int, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if set(row) != set(FIELDS) or any(
            value is not None and type(value) is not int for value in row.values()
        ):
            raise ValueError("Only the numeric admissions projection is allowed")
        if any(type(row[key]) is not int or row[key] < 1 for key in ("id", "school_id", "year")):
            raise ValueError("Invalid admission record identity")
        if any(type(row[key]) is not int for key in group_by):
            raise ValueError("Missing grouping identity")
        groups[tuple(row[key] for key in group_by)].append(row)
    if len(groups) > MAX_GROUPS:
        raise ValueError("Too many groups: narrow the cohort")
    results = []
    for keys, records in sorted(groups.items()):
        scores = [row["min_score"] for row in records if row["min_score"] is not None]
        ranks = [row["min_rank"] for row in records if row["min_rank"] is not None]
        results.append(
            {
                "key": list(keys),
                "records": len(records),
                "missing_min_scores": len(records) - len(scores),
                "median_min_score": statistics.median(scores) if scores else None,
                "lowest_min_score": min(scores) if scores else None,
                "highest_min_score": max(scores) if scores else None,
                "best_min_rank": min(ranks) if ranks else None,
                "worst_min_rank": max(ranks) if ranks else None,
            }
        )
    return {"groups": results}


def _command(image_id: str, root: Path, name: str) -> list[str]:
    if not _IMAGE.fullmatch(image_id):
        raise ValueError("Use a locally prebuilt immutable Docker image ID")
    if any(char in str(root) for char in (",", "\n", "\r", '"')):
        raise ValueError("Invalid temporary mount path")
    return [
        "docker", "run", "--rm", "--pull=never", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "48",
        "--ulimit", "nofile=64:64", "--memory", "256m", "--memory-swap", "256m",
        "--ipc", "none", "--cpus", "1", "--user", "65534:65534",
        "--log-driver", "none", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--mount", f"type=bind,src={root},dst=/input,readonly",
        "--workdir", "/tmp", "--env", "HOME=/tmp", "--entrypoint", "python",
        image_id, "-I", "-B", "/input/analysis.py",
    ]


def execute_admissions_analysis(
    rows: list[dict[str, Any]],
    scope: Scope,
    group_by: tuple[str, ...],
    program: bytes,
    *,
    image_id: str,
    approval_ref: str,
    authorized: bool = False,
) -> dict[str, Any]:
    """Execute one bounded Python tool call; never return unverified model output."""
    if authorized is not True or not isinstance(approval_ref, str) or not 1 <= len(approval_ref.strip()) <= 200:
        raise ValueError("Explicit operator authorization and data approval reference required")
    if not isinstance(program, bytes) or not 1 <= len(program) <= MAX_CODE_BYTES:
        raise ValueError("Invalid analysis program size")
    if not _IMAGE.fullmatch(image_id):
        raise ValueError("Use a locally prebuilt immutable Docker image ID")
    if scope.level == "school" and "major_id" in group_by:
        raise ValueError("Major grouping requires major-level records")
    expected = _expected(rows, group_by)
    if any(
        not scope.year_from <= row["year"] <= scope.year_to
        or (scope.school_id is not None and row["school_id"] != scope.school_id)
        or (row["major_id"] is None) != (scope.level == "school")
        for row in rows
    ):
        raise ValueError("Projection does not match approved cohort")
    payload = _json({"rows": rows, "group_by": group_by})
    receipt = {
        "schema": "admissions-analysis/v1",
        "scope": asdict(scope),
        "group_by": list(group_by),
        "row_count": len(rows),
        "input_sha256": _digest(payload),
        "code_sha256": _digest(program),
        "approval_ref_sha256": _digest(approval_ref.encode()),
        "image_id": image_id,
        "status": "BLOCKED",
        "admissions_recommendation_verified": False,
    }
    name = "gaobao-analysis-" + secrets.token_hex(8)
    with tempfile.TemporaryDirectory(prefix="gaobao-analysis-") as parent:
        root = Path(parent) / "input"
        root.mkdir()
        (root / "rows.json").write_bytes(payload)
        (root / "analysis.py").write_bytes(program)
        for path in root.iterdir():
            path.chmod(0o444)
        root.chmod(0o555)
        try:
            try:
                result = _bounded_docker_run(_command(image_id, root, name), timeout=25)
                if result.returncode != 0:
                    receipt["error"] = "execution_failed"
                else:
                    # Exact canonical equality prevents invented metrics, extra
                    # fields and JSON booleans masquerading as numbers.
                    candidate = json.loads(result.stdout)
                    if _json(candidate) != _json(expected):
                        receipt["error"] = "oracle_mismatch"
                    else:
                        receipt["status"] = "VERIFIED_DESCRIPTIVE_ANALYSIS"
            except subprocess.TimeoutExpired:
                receipt["error"] = "sandbox_timeout"
            except SandboxOutputExceeded:
                receipt["error"] = "sandbox_output_limit"
            except (ValueError, TypeError, RecursionError):
                receipt["error"] = "invalid_analysis_output"
            except OSError:
                receipt["error"] = "sandbox_unavailable"
            finally:
                receipt["cleanup_verified"] = _remove_and_verify_container(name)
                if not receipt["cleanup_verified"]:
                    receipt["status"] = "BLOCKED"
                    receipt["error"] = "sandbox_cleanup_unverified"
        finally:
            root.chmod(0o700)
    if receipt["status"] == "VERIFIED_DESCRIPTIVE_ANALYSIS":
        receipt["result"] = expected
        receipt["result_sha256"] = _digest(_json(expected))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-data", action="store_true", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--province", required=True)
    parser.add_argument("--subject-type", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--year-from", type=int, required=True)
    parser.add_argument("--year-to", type=int, required=True)
    parser.add_argument("--level", choices=("school", "major"), default="school")
    parser.add_argument("--school-id", type=int)
    parser.add_argument("--group-by", choices=("year", "school_id", "major_id"), nargs="+", required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not os.environ.get("DATABASE_URL", "").strip():
        parser.error("Set DATABASE_URL explicitly to an authorized admissions database")
    scope = Scope(
        args.province, args.subject_type, args.batch, args.year_from, args.year_to, args.level, args.school_id
    )
    with args.code.open("rb") as handle:
        program = handle.read(MAX_CODE_BYTES + 1)
    # Reserve a private fresh receipt before contacting the database or Docker.
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        try:
            from db.database import get_session

            with get_session() as session:
                rows = collect_admissions_rows(session, scope)
            receipt = execute_admissions_analysis(
                rows, scope, tuple(args.group_by), program,
                image_id=args.image, approval_ref=args.approval_ref, authorized=args.authorized_data,
            )
        except Exception:
            # No DB URL, SQL exception, generated program or raw records in receipt.
            receipt = {"schema": "admissions-analysis/v1", "status": "BLOCKED", "error": "preflight_failed"}
        handle.write(_json(receipt) + b"\n")
    return 0 if receipt["status"] == "VERIFIED_DESCRIPTIVE_ANALYSIS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
