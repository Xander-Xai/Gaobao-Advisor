"""The opt-in model planner is a real API adapter, not a prewritten repair script.

All tests inject a fake client and never spend tokens or reach an external URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarks.sandbox_coding_agent import CodingAction, run_repair
from benchmarks.sandbox_model_planner import JSONCodingPlanner

IMAGE = "sha256:" + "a" * 64


class StubChat:
    def __init__(self) -> None:
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        observations = json.loads(kwargs["messages"][1]["content"])["bounded_observations"]
        if not observations:
            action = {"kind": "inspect"}
        elif observations[-1]["kind"] == "inspect":
            action = {"kind": "edit", "content": "def add(a, b):\n    return a + b\n"}
        elif observations[-1]["kind"] == "edit":
            action = {"kind": "test"}
        else:
            action = {"kind": "finish"}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(action)))],
            usage=SimpleNamespace(prompt_tokens=13, completion_tokens=7),
        )


def test_model_planner_consumes_observations_and_reports_provider_tokens(monkeypatch):
    stub = StubChat()
    client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    planner = JSONCodingPlanner(client, "fake-planner")
    action = planner.decide("fictional task", [])
    assert action.kind == "inspect"
    assert action.prompt_tokens == 13
    assert action.completion_tokens == 7
    assert stub.requests[-1]["response_format"] == {"type": "json_object"}
    assert stub.requests[-1]["temperature"] == 0

    def fake_sandbox(_image, work, *, oracle_source):
        assert "import importlib.util" in oracle_source
        assert "SYNTHETIC_WORKSPACE_PASS" in oracle_source
        good = "return a + b" in (work / "solution.py").read_text(encoding="utf-8")
        if good:
            (work / "artifact.txt").write_text("PASS", encoding="utf-8")
        return {"passed": good}

    monkeypatch.setattr("benchmarks.sandbox_coding_agent.run_sandbox_probe", fake_sandbox)
    result = run_repair("addition", IMAGE, planner, require_provider_usage=True)
    assert result["status"] == "completed"
    assert result["tool_sequence"] == ["inspect", "edit", "test"]
    assert result["model_steps"] == 4
    assert result["prompt_tokens"] == 52
    assert result["completion_tokens"] == 28


def test_unknown_usage_is_blocked_before_executing_model_action(monkeypatch):
    class MissingUsage:
        def decide(self, task, observations):
            return CodingAction("edit", "def add(a, b):\n return a+b\n")

    result = run_repair("addition", IMAGE, MissingUsage(), require_provider_usage=True)
    assert result["status"] == "blocked"
    assert result["reason"] == "provider_usage_missing"
    assert result["tool_calls"] == 0


def test_budget_exhaustion_blocks_edit_before_execution():
    class Expensive:
        def decide(self, task, observations):
            return CodingAction("edit", "def add(a,b):\n return a+b\n", 40, 40)

    result = run_repair("addition", IMAGE, Expensive(), max_provider_tokens=50, require_provider_usage=True)
    assert result["reason"] == "provider_token_budget_exhausted"
    assert result["tool_calls"] == 0


def test_cli_refuses_model_billing_without_permission(tmp_path):
    environ = dict(os.environ)
    environ.pop("OPENAI_API_KEY", None)
    base = [
        sys.executable,
        "-m",
        "benchmarks.sandbox_coding_agent",
        "--image",
        IMAGE,
        "--out",
        str(tmp_path / "result.json"),
        "--model",
        "candidate-model",
    ]
    result = subprocess.run(base, capture_output=True, text=True, check=False, timeout=12, env=environ)
    assert result.returncode != 0
    assert "--permit-external-model" in result.stderr
    assert not (tmp_path / "result.json").exists()


def test_model_requires_api_key_even_with_permission(tmp_path):
    environ = dict(os.environ)
    environ.pop("OPENAI_API_KEY", None)
    cmd = [
        sys.executable,
        "-m",
        "benchmarks.sandbox_coding_agent",
        "--image",
        IMAGE,
        "--out",
        str(tmp_path / "result.json"),
        "--model",
        "candidate-model",
        "--permit-external-model",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=12, env=environ)
    assert result.returncode != 0
    assert "OPENAI_API_KEY" in result.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        '{"kind":"shell","command":"whoami"}',
        '{"kind":"edit","content":123}',
        '{"kind":"inspect","content":"disallowed"}',
        '{"kind":"finish","arbitrary":true}',
    ],
)
def test_model_planner_rejects_unallowlisted_actions(payload):
    class Responses:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Responses()))
    planner = JSONCodingPlanner(client, "model")
    with pytest.raises(ValueError, match="unsupported|non-string|include"):
        planner.decide("synthetic task", [])
