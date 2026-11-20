"""No-key subprocess smoke: run the real graph in an isolated synthetic database."""

from __future__ import annotations

import json
import subprocess
import sys


def test_real_graph_offline_synthetic_replay(tmp_path):
    report_path = tmp_path / "agent-eval.json"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.agent_eval_replay",
            "--out",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert report_path.exists(), f"Replay did not produce a sanitized report (exit={process.returncode})"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["environment"]["data_profile"] == "SYNTHETIC_DEMO"
    assert report["environment"]["network"] == "blocked"
    assert report["summary"]["cases"] == 3
    assert process.returncode == 0, f"Replay contract failures: {report['summary']['failures_by_code']}"
    assert report["summary"]["passed"] == 3
