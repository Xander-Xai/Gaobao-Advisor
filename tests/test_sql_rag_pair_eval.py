"""Read-only, isolated SQL/RAG performance probe contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from benchmarks.sql_rag_pair_eval import _percentile


def test_percentile_interpolates_sorted_samples():
    assert _percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert _percentile([1, 2, 3, 4, 5], 0.95) == 4.8


def test_isolated_synthetic_sql_rag_pair_cli(tmp_path: Path):
    report = tmp_path / "paired.json"
    env = dict(os.environ)
    env["APP_ENV"] = "development"
    # Should be ignored; the CLI MUST replace this path with a new temp DB.
    env["DATABASE_URL"] = "sqlite:////tmp/unrelated_app_database_must_not_open.sqlite"
    cmd = [
        sys.executable,
        "-m",
        "benchmarks.sql_rag_pair_eval",
        "--repeat",
        "2",
        "--warmup",
        "0",
        "--out",
        str(report),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120, check=False)
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr)[-4000:]
    metrics = json.loads(report.read_text(encoding="utf-8"))
    assert metrics["data_profile"] == "SYNTHETIC_DEMO"
    assert metrics["isolated_temp_sqlite"] is True
    assert metrics["network_provider_calls"] is False
    assert metrics["output_equivalent"] is True
    assert metrics["sql_error_free"] is True
    assert metrics["real_concurrent_load_gain"] == "NOT_MEASURED"
    assert metrics["production_parallelization_authorized"] is False
    assert not Path("/tmp/unrelated_app_database_must_not_open.sqlite").exists()


def test_sql_rag_pair_cli_refuses_production(tmp_path: Path):
    env = dict(os.environ)
    env["APP_ENV"] = "production"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.sql_rag_pair_eval",
            "--repeat",
            "1",
            "--out",
            str(tmp_path / "prod.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert "cannot run in production" in proc.stderr
    assert not (tmp_path / "prod.json").exists()
