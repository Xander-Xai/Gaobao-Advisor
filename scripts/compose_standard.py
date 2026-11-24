"""Canonical local synthetic deployment; all infrastructure layers are required."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if not (root / ".env.full").is_file():
        print("Run python scripts/setup_full_env.py first.", file=sys.stderr)
        return 2
    if len(sys.argv) < 2:
        print("Usage: python scripts/compose_standard.py up --build -d", file=sys.stderr)
        return 2
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.full",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.full.yml",
        "-f",
        "docker-compose.java.yml",
        "--profile",
        "full",
        "--profile",
        "java",
        *sys.argv[1:],
    ]
    return subprocess.call(command, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
