"""The real-model probe may never silently call an external provider in CI."""

import subprocess
import sys


def test_model_probe_requires_explicit_external_call_consent(tmp_path):
    command = [
        sys.executable,
        "-m",
        "benchmarks.readonly_model_eval",
        "--out",
        str(tmp_path / "should-not-exist.json"),
        "--model",
        "fake",
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert process.returncode != 0
    assert "--permit-external-model" in process.stderr
    assert not (tmp_path / "should-not-exist.json").exists()
