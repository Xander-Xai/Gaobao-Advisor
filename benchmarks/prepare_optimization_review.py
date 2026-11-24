"""Prepare an offline, operator-reviewed proposal from completed paired model runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from benchmarks.repeated_model_experiment import ARMS
from benchmarks.synthetic_optimization_review import build_optimization_review

MAX_INPUT_BYTES = 160_000


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load_pairs(directory: Path, trials: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not 2 <= trials <= 5 or not directory.is_dir():
        raise ValueError("Expected an existing experiment directory and 2..5 trials")
    expected = {f"trial-{index}-{arm}.json" for index in range(1, trials + 1) for arm in ARMS}
    found = {path.name for path in directory.glob("trial-*.json")}
    if expected != found:
        raise ValueError("Incomplete or unexpected paired trial files")
    remaining = MAX_INPUT_BYTES
    pairs = []
    for index in range(1, trials + 1):
        reports = []
        for arm in ARMS:
            path = directory / f"trial-{index}-{arm}.json"
            if path.is_symlink() or not path.is_file():
                raise ValueError("Expected a regular report file")
            with path.open("rb") as handle:
                raw = handle.read(remaining + 1)
            remaining -= len(raw)
            if remaining < 0:
                raise ValueError("Experiment reports exceed input budget")
            report = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(report, dict):
                raise ValueError("Invalid model report")
            reports.append(report)
        pairs.append((reports[0], reports[1]))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--trials", type=int, required=True)
    parser.add_argument("--submission-out", type=Path, help="New private file for manual Admin API submission")
    args = parser.parse_args(argv)
    try:
        pairs = load_pairs(args.run_dir, args.trials)
        review = build_optimization_review(pairs)
        if args.submission_out is not None:
            payload = json.dumps({"pairs": pairs}, ensure_ascii=False, allow_nan=False)
            fd = os.open(args.submission_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        parser.error("Invalid, incomplete, oversized or unavailable experiment inputs/output")
    print(json.dumps(review, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
