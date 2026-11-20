"""P30 Observability: finite labels, worker exporter lifecycle and safe outcomes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from server.agent import taskrun_worker
from server.metrics import _metric_endpoint
from server.observability.agent_trace import (
    _TASKRUN_ATTEMPTS,
    record_taskrun_outcome,
    safe_attributes,
)


def test_metrics_never_use_caller_controlled_path_as_label():
    private = "syn_PRIVATE_Student_550"
    request = Request(
        {"type": "http", "method": "GET", "path": f"/api/v1/admin/agent-taskruns/runs/{private}", "headers": []}
    )
    assert _metric_endpoint(request) == "/api/v1/admin/agent-*"
    request.scope["route"] = SimpleNamespace(path="/api/v1/admin/agent-taskruns/runs/{run_id}")
    assert _metric_endpoint(request) == "/api/v1/admin/agent-taskruns/runs/{run_id}"
    request.scope["route"] = None
    request.scope["path"] = f"/not-an-api/{private}"
    assert _metric_endpoint(request) == "other"


def test_taskrun_metrics_use_fixed_label_values_even_on_malicious_error():
    record_taskrun_outcome("deepagents", "FAILED", "credential-in-error-message")
    record_taskrun_outcome("deepagents", "SUCCEEDED", None)
    matching = [
        sample
        for sample in _TASKRUN_ATTEMPTS.collect()[0].samples
        if sample.labels.get("engine") == "deepagents"
        and sample.labels.get("status") in {"FAILED", "SUCCEEDED"}
    ]
    assert matching
    assert any(sample.labels["reason"] == "other" for sample in matching)
    assert all("credential-" not in str(sample.labels) for sample in matching)
    assert safe_attributes({"agent.status": "user-controlled-secret"}) == {"agent.status": "unknown"}


def test_worker_cli_initializes_and_flushes_exporter(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(taskrun_worker, "SyntheticTaskRunStore", lambda path: SimpleNamespace(path=path))
    monkeypatch.setattr(taskrun_worker, "initialize_agent_otel", lambda: calls.append("init") or True)
    monkeypatch.setattr(taskrun_worker, "shutdown_agent_otel", lambda: calls.append("shutdown"))

    def successful(store, run_id, **kwargs):
        calls.append("run")
        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "engine": "portfolio",
            "attempts": 1,
            "failure_code": None,
        }

    monkeypatch.setattr(taskrun_worker, "execute_approved_run", successful)
    assert taskrun_worker.main(["--journal", str(tmp_path / "journal.sqlite"), "--run-id", "syn_test"]) == 0
    assert calls == ["init", "run", "shutdown"]
    assert '"status": "SUCCEEDED"' in capsys.readouterr().out


def test_worker_flushes_exporter_when_execution_rejected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(taskrun_worker, "SyntheticTaskRunStore", lambda path: SimpleNamespace(path=path))
    monkeypatch.setattr(taskrun_worker, "initialize_agent_otel", lambda: calls.append("init") or True)
    monkeypatch.setattr(taskrun_worker, "shutdown_agent_otel", lambda: calls.append("shutdown"))

    def denied(store, run_id, **kwargs):
        calls.append("run")
        raise ValueError("private-diagnostic-details")

    monkeypatch.setattr(taskrun_worker, "execute_approved_run", denied)
    with pytest.raises(SystemExit) as error:
        taskrun_worker.main(["--journal", str(tmp_path / "journal.sqlite"), "--run-id", "syn_test"])
    assert error.value.code == 2
    assert calls == ["init", "run", "shutdown"]


def test_worker_wrapper_records_stored_status_not_exception_text(monkeypatch):
    class Store:
        def get(self, run_id):
            return {"engine": "deterministic"}

        def get_internal_traceparent(self, run_id):
            return None

    monkeypatch.setattr(
        taskrun_worker,
        "_execute_approved_run",
        lambda store, run_id, **kwargs: {"status": "FAILED", "failure_code": "agent_execution_failed"},
    )
    taskrun_worker.execute_approved_run(Store(), "synthetic-run")
    matches = [
        x
        for x in _TASKRUN_ATTEMPTS.collect()[0].samples
        if x.labels.get("engine") == "deterministic"
        and x.labels.get("status") == "FAILED"
        and x.labels.get("reason") == "execution_failed"
    ]
    assert matches
