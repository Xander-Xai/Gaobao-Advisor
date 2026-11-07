"""Regression: HA probe must tolerate transient timeouts but not mask real failures."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_FILE = Path(__file__).resolve().parents[1] / "scripts/verify_java_distributed_ci.py"
SPEC = importlib.util.spec_from_file_location("gaobao_distributed_ci", SCRIPT_FILE)
assert SPEC is not None and SPEC.loader is not None
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class HttpRetryTests(unittest.TestCase):
    def test_transient_timeouts_are_retried_until_success(self):
        calls = []

        def flaky(url, *, timeout=8):
            calls.append(url)
            if len(calls) <= 2:
                raise TimeoutError("timed out")
            return 200, {}, "ok"

        with patch.object(ci, "http", side_effect=flaky), patch.object(ci.time, "sleep", return_value=None):
            code, _, body = ci.http_with_retry("http://127.0.0.1/api/v1/health", timeout=8)
        self.assertEqual(code, 200)
        self.assertEqual(body, "ok")
        self.assertEqual(len(calls), 3)

    def test_persistent_timeout_raises_after_bounded_attempts(self):
        calls = []

        def always_timeout(url, *, timeout=8):
            calls.append(url)
            raise TimeoutError("timed out")

        with patch.object(ci, "http", side_effect=always_timeout), patch.object(ci.time, "sleep", return_value=None):
            with self.assertRaises(TimeoutError):
                ci.http_with_retry("http://127.0.0.1/api/v1/health", timeout=8, attempts=4)
        self.assertEqual(len(calls), 4)

    def test_non_timeout_errors_are_not_retried(self):
        calls = []

        def boom(url, *, timeout=8):
            calls.append(url)
            raise OSError("network unreachable")

        with patch.object(ci, "http", side_effect=boom), patch.object(ci.time, "sleep", return_value=None):
            with self.assertRaises(OSError):
                ci.http_with_retry("http://127.0.0.1/api/v1/health", timeout=8)
        self.assertEqual(len(calls), 1)

    def test_http_non_200_returns_without_retry(self):
        calls = []

        def error_status(url, *, timeout=8):
            calls.append(url)
            return 500, {}, "boom"

        with patch.object(ci, "http", side_effect=error_status), patch.object(ci.time, "sleep", return_value=None):
            code, _, _ = ci.http_with_retry("http://127.0.0.1/api/v1/health", timeout=8)
        self.assertEqual(code, 500)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
