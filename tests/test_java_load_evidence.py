"""Regression tests for honest Java/Python load evidence accounting."""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SUITE_FILE = Path(__file__).resolve().parents[1] / "benchmarks/load/run_suite.py"
SPEC = importlib.util.spec_from_file_location("gaobao_load_suite", SUITE_FILE)
assert SPEC is not None and SPEC.loader is not None
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)


def _locust_result(requests: int, failures: int):
    def run(args, **_kwargs):
        prefix = Path(args[args.index("--csv") + 1])
        with (prefix.parent / f"{prefix.name}_stats.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "Name",
                    "Request Count",
                    "Failure Count",
                    "Requests/s",
                    "50%",
                    "95%",
                    "99%",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Name": "Aggregated",
                    "Request Count": requests,
                    "Failure Count": failures,
                    "Requests/s": 0 if not requests else 1,
                    "50%": 0,
                    "95%": 0,
                    "99%": 0,
                }
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    return run


class LoadEvidenceTests(unittest.TestCase):
    def test_informational_zero_completions_are_recorded_without_fake_error_rate(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with (
                patch.object(suite.subprocess, "run", side_effect=_locust_result(0, 0)),
                patch.object(suite, "_monitor", return_value=None),
            ):
                result = suite._run(
                    "python-overview", 50, 8, "dual-gateway", "http://localhost", output, informational=True
                )
            self.assertEqual(result["result_status"], "NO_COMPLETED_REQUESTS")
            self.assertEqual(result["request_count"], 0)
            self.assertIsNone(result["error_rate_pct"])
            for percentile in ("p50_ms", "p95_ms", "p99_ms"):
                self.assertIsNone(result[percentile])
            self.assertTrue(result["informational"])
            report = json.loads((output / "dual-gateway-python-overview-50u.json").read_text())
            self.assertEqual(report["result_status"], "NO_COMPLETED_REQUESTS")

    def test_zero_completions_still_fail_required_scenarios_but_keep_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with (
                patch.object(suite.subprocess, "run", side_effect=_locust_result(0, 0)),
                patch.object(suite, "_monitor", return_value=None),
            ):
                with self.assertRaisesRegex(AssertionError, "No completed requests"):
                    suite._run("catalog", 50, 8, "dual-gateway", "http://localhost", output)
            self.assertTrue((output / "dual-gateway-catalog-50u.json").exists())

    def test_informational_all_failed_is_explicit_not_measured_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(suite.subprocess, "run", side_effect=_locust_result(5, 5)),
                patch.object(suite, "_monitor", return_value=None),
            ):
                result = suite._run(
                    "python-overview", 50, 8, "dual-gateway", "http://localhost", Path(temp), informational=True
                )
            self.assertEqual(result["result_status"], "ALL_FAILED")
            self.assertEqual(result["error_rate_pct"], 100.0)

    def test_all_failed_required_scenario_is_a_real_ci_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with (
                patch.object(suite.subprocess, "run", side_effect=_locust_result(4, 4)),
                patch.object(suite, "_monitor", return_value=None),
            ):
                with self.assertRaisesRegex(AssertionError, "All completed requests failed"):
                    suite._run("chat", 5, 8, "dual-gateway", "http://localhost", output)
            report = json.loads((output / "dual-gateway-chat-5u.json").read_text())
            self.assertEqual(report["result_status"], "ALL_FAILED")
            self.assertEqual(report["error_rate_pct"], 100.0)

    def test_explicit_observational_catalog_records_zero_without_ci_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with (
                patch.object(suite.subprocess, "run", side_effect=_locust_result(0, 0)),
                patch.object(suite, "_monitor", return_value=None),
            ):
                result = suite._run("catalog", 50, 8, "dual-gateway", "http://localhost", output, informational=True)
            self.assertEqual(result["result_status"], "NO_COMPLETED_REQUESTS")
            self.assertIsNone(result["error_rate_pct"])
            self.assertTrue((output / "dual-gateway-catalog-50u.json").exists())


if __name__ == "__main__":
    unittest.main()
