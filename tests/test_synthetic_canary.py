"""Canary rollback actually changes synthetic model-eval prompt selection."""

from __future__ import annotations

import pytest

from server.agent.synthetic_canary import choose_synthetic_variant

PID = "opt_" + "a" * 32
IDENTIFIER = "synthetic-canary-reproducible-id-01"


class Registry:
    def __init__(self):
        self.active = True
        self.status = "DEMO_POINTER_ACTIVE"

    def demo_snapshot(self):
        return {"active_demo_pointer": PID if self.active else None}

    def get(self, proposal):
        assert proposal == PID
        return {
            "status": self.status,
            "report": {"evaluation_gate": "REVIEW_REQUIRED"},
            "signoffs": [
                {"reviewer": "admin-one", "verdict": "attest_reviewed"},
                {"reviewer": "admin-two", "verdict": "attest_reviewed"},
            ],
        }


def test_canary_disabled_and_rollback_are_real_routing_controls(monkeypatch):
    registry = Registry()
    monkeypatch.delenv("ENABLE_SYNTHETIC_AGENT_CANARY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    assert choose_synthetic_variant(registry, IDENTIFIER, 100)["prompt_variant"] == "baseline"
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_CANARY", "true")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("APP_ENV", "development")
    selected = choose_synthetic_variant(registry, IDENTIFIER, 100)
    assert selected["prompt_variant"] == "evidence_first"
    assert selected["allocation"] == "SYNTHETIC_CANDIDATE"
    assert selected["production_canary"] is False
    assert choose_synthetic_variant(registry, IDENTIFIER, 100) == selected
    assert choose_synthetic_variant(registry, IDENTIFIER, 0)["prompt_variant"] == "baseline"
    registry.active = False
    assert choose_synthetic_variant(registry, IDENTIFIER, 100)["allocation"] == "ROLLED_BACK_OR_UNSTAGED"


def test_cannot_override_invalid_review_and_security(monkeypatch):
    registry = Registry()
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_CANARY", "true")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("APP_ENV", "development")
    registry.status = "REJECTED_BY_ADMIN"
    with pytest.raises(ValueError, match="eligible"):
        choose_synthetic_variant(registry, IDENTIFIER, 100)
    registry.status = "DEMO_POINTER_ACTIVE"
    for key in ("session:user-1", "normal-user", "", "../escape"):
        with pytest.raises(ValueError):
            choose_synthetic_variant(registry, key)
    with pytest.raises(ValueError):
        choose_synthetic_variant(registry, IDENTIFIER, 101)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="production"):
        choose_synthetic_variant(registry, IDENTIFIER, 100)


def test_cli_cannot_start_canary_through_deterministic_mode(tmp_path):
    from benchmarks.decision_card_business_eval import main

    with pytest.raises(SystemExit):
        main(
            [
                "--out",
                str(tmp_path / "result.json"),
                "--mode",
                "baseline",
                "--synthetic-canary-journal",
                str(tmp_path / "journal.sqlite"),
                "--canary-run-key",
                IDENTIFIER,
            ]
        )
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("env", ["", "production", "staging", "prod"])
def test_canary_rejects_unknown_environment(monkeypatch, env):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_CANARY", "true")
    with pytest.raises(ValueError, match="non-production"):
        choose_synthetic_variant(Registry(), IDENTIFIER)


def test_percentage_alone_cannot_be_silently_ignored(tmp_path):
    from benchmarks.decision_card_business_eval import main

    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path / "result.json"), "--canary-percentage", "0"])
    assert not (tmp_path / "result.json").exists()


def test_duplicate_reviewer_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_CANARY", "true")
    registry = Registry()
    get = registry.get

    def duplicate(proposal):
        result = get(proposal)
        result["signoffs"][1]["reviewer"] = result["signoffs"][0]["reviewer"]
        return result

    monkeypatch.setattr(registry, "get", duplicate)
    with pytest.raises(ValueError, match="eligible"):
        choose_synthetic_variant(registry, IDENTIFIER)


@pytest.mark.parametrize("alias", ["journal", "report", "hardlink"])
def test_canary_preflight_preserves_existing_output(tmp_path, monkeypatch, alias):
    from benchmarks.decision_card_business_eval import main

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-fixture-only")
    journal = tmp_path / "journal.sqlite"
    journal.write_bytes(b"fixture journal")
    output = tmp_path / "report.json"
    if alias == "journal":
        output = journal
    elif alias == "hardlink":
        try:
            output.hardlink_to(journal)
        except (OSError, NotImplementedError):
            pytest.skip("Hard links unavailable")
    else:
        output.write_bytes(b"previous report")
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "--out",
                str(output),
                "--mode",
                "model",
                "--model",
                "fixture",
                "--permit-external-model",
                "--synthetic-canary-journal",
                str(journal),
                "--canary-run-key",
                IDENTIFIER,
            ]
        )
    assert journal.read_bytes() == b"fixture journal"
    assert output.read_bytes() == before
