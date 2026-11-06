"""Locust throughput/latency report with Docker CPU and memory snapshots.

This measures a single Docker host using SYNTHETIC_DEMO and no real LLM key.
An HTTP failure remains in the reported count; no SLA is assumed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CLASSES = {
    "catalog": "CatalogUser",
    "python": "PythonCatalogUser",
    "python-overview": "PythonOverviewUser",
    "chat": "NoKeyChatUser",
}
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "gaobao-java-smoke",
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
]


def _number(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _memory_mib(value: str) -> float:
    match = re.search(r"([\d.]+)(B|KiB|MiB|GiB|TiB)", value.split("/", 1)[0])
    if not match:
        return 0.0
    factors = {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1048576}
    return round(float(match.group(1)) * factors[match.group(2)], 2)


def _docker_sample() -> list[dict]:
    ps = subprocess.run(
        COMPOSE + ["ps", "-q", "nginx", "java-gateway", "java-catalog", "api", "mysql", "redis"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    ids = ps.stdout.split()
    if not ids:
        return []
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    rows = []
    for line in result.stdout.splitlines():
        item = json.loads(line)
        rows.append(
            {
                "container": item["Name"],
                "cpu_percent": _number(item["CPUPerc"].rstrip("%")),
                "memory_mib": _memory_mib(item["MemUsage"]),
            }
        )
    return rows


def _monitor(stop: threading.Event, samples: list[dict]) -> None:
    while not stop.is_set():
        try:
            rows = _docker_sample()
            if rows:
                samples.append({"at": datetime.now(timezone.utc).isoformat(), "containers": rows})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
        stop.wait(2)


def _run(
    scenario: str, users: int, duration: int, label: str, host: str, directory: Path, informational: bool = False
) -> dict:
    prefix = directory / f"{label}-{scenario}-{users}u"
    stop = threading.Event()
    samples: list[dict] = []
    sampler = threading.Thread(target=_monitor, args=(stop, samples), daemon=True)
    sampler.start()
    args = [
        "locust",
        "-f",
        "benchmarks/load/locustfile.py",
        "--headless",
        "--host",
        host,
        "--users",
        str(users),
        "--spawn-rate",
        str(max(10, users)),
        "--run-time",
        f"{duration}s",
        "--stop-timeout",
        "8",
        "--only-summary",
        "--exit-code-on-error",
        "0",
        "--csv",
        str(prefix),
        CLASSES[scenario],
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=duration + 80, check=False)
    finally:
        stop.set()
        sampler.join(timeout=23)
    if completed.returncode != 0:
        raise RuntimeError(f"Locust process failed: {completed.returncode}: {completed.stderr[-1200:]}")
    with (directory / f"{prefix.name}_stats.csv").open(encoding="utf-8-sig", newline="") as file:
        records = list(csv.DictReader(file))
    total = next((row for row in records if row.get("Name") == "Aggregated"), None)
    if total is None:
        raise AssertionError(f"Missing Locust aggregate row: {records}")
    count = int(_number(total.get("Request Count")))
    failed = int(_number(total.get("Failure Count")))
    status = (
        "NO_COMPLETED_REQUESTS"
        if count == 0
        else "ALL_FAILED"
        if failed == count
        else "MEASURED_WITH_ERRORS"
        if failed
        else "MEASURED"
    )
    entries = [entry for sample in samples for entry in sample["containers"]]
    metric = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": os.getenv("GITHUB_SHA", "local"),
        "scenario": scenario,
        "informational": informational,
        "result_status": status,
        "deployment": label,
        "users": users,
        "configured_duration_s": duration,
        "wall_seconds": round(time.monotonic() - started, 2),
        "request_count": count,
        "failure_count": failed,
        "error_rate_pct": round(100 * failed / count, 2) if count else None,
        "rps": round(_number(total.get("Requests/s")), 2),
        "p50_ms": _number(total.get("50%")),
        "p95_ms": _number(total.get("95%")),
        "p99_ms": _number(total.get("99%")),
        "peak_container_cpu_pct": max((e["cpu_percent"] for e in entries), default=None),
        "peak_container_memory_mib": max((e["memory_mib"] for e in entries), default=None),
        "docker_resource_snapshots": samples,
        "scope": "single GitHub Actions Docker host; synthetic data; no real LLM",
    }
    (directory / f"{prefix.name}.json").write_text(
        json.dumps(metric, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{label}/{scenario} users={users} requests={count} RPS={metric['rps']} "
        f"p50/p95/p99={metric['p50_ms']}/{metric['p95_ms']}/{metric['p99_ms']}ms "
        f"failed={failed}({metric['error_rate_pct']}%) status={status} "
        f"CPU-peak={metric['peak_container_cpu_pct']}% "
        f"mem-peak={metric['peak_container_memory_mib']}MiB",
        flush=True,
    )
    if not count:
        print(
            f"WARNING: {scenario} has zero completed requests; error rate and latency are "
            "not measurable. A short test window can end before client timeouts finish.",
            flush=True,
        )
        if not informational:
            raise AssertionError(f"No completed requests in {scenario}")
    elif informational and failed == count:
        print(f"WARNING: {scenario} completed requests all failed", flush=True)
    return metric


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=sorted(CLASSES))
    parser.add_argument("--tiers", required=True, help="Comma-separated users, e.g. 50,100,200")
    parser.add_argument("--label", default="dual")
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--host", default="http://127.0.0.1")
    parser.add_argument("--output", default="benchmarks/load/results")
    parser.add_argument(
        "--informational",
        action="store_true",
        help="Record an overloaded python-overview sample without failing functional CI",
    )
    opts = parser.parse_args()
    if opts.informational and opts.scenario != "python-overview":
        parser.error("--informational may only be used with python-overview")
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,50}", opts.label):
        parser.error("--label must be alphanumeric/hyphens")
    if not 5 <= opts.duration <= 600:
        parser.error("duration must be between 5 and 600 seconds")
    tiers = [int(item) for item in opts.tiers.split(",")]
    if not tiers or any(not 1 <= n <= 500 for n in tiers):
        parser.error("users must be between 1 and 500")
    output = Path(opts.output)
    output.mkdir(parents=True, exist_ok=True)
    metrics = [_run(opts.scenario, n, opts.duration, opts.label, opts.host, output, opts.informational) for n in tiers]
    (output / f"{opts.label}-{opts.scenario}-summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
