"""The opt-in planner discloses trusted skills by execution stage, without network."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server.agent.readonly_model_planner import OpenAIJsonPlanner
from skills.service import SkillAssetUnavailableError, SkillContextBudgetExceeded, SkillService


def _client():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"abstain"}'))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
    )
    return client


def _service(tmp_path, *, safety="SAFE"):
    directory = tmp_path / "gaokao"
    directory.mkdir()
    for name, content in {
        "safety_rules": safety,
        "mental_models": "INITIAL_MODEL",
        "heuristics": "OBSERVED_HEURISTIC",
        "anti_patterns": "OBSERVED_NEGATIVE",
    }.items():
        (directory / f"{name}.md").write_text(content, encoding="utf-8")
    return SkillService(skills_dir=tmp_path)


def _messages(client):
    return client.chat.completions.create.call_args.kwargs["messages"]


def test_default_baseline_unchanged_and_no_skill_receipt(tmp_path):
    client = _client()
    planner = OpenAIJsonPlanner(client, "test", skill_service=SkillService(skills_dir=tmp_path))
    planner.decide("fictional", [], {})
    assert "read-only synthetic admissions assistant" in _messages(client)[0]["content"]
    assert planner.skill_policy_receipt() == {"policy": "baseline", "context_sha256": {}, "context_chars": {}}


def test_staged_policy_reaches_model_and_keeps_safety(tmp_path):
    client = _client()
    planner = OpenAIJsonPlanner(client, "test", skill_policy="staged-v1", skill_service=_service(tmp_path))
    schemas = {"school_lookup": {"school_name": "str"}}
    planner.decide("fictional", [], schemas)
    initial = _messages(client)[0]["content"]
    assert "SAFE" in initial and "INITIAL_MODEL" in initial
    assert "OBSERVED_HEURISTIC" not in initial and "OBSERVED_NEGATIVE" not in initial
    planner.decide("fictional", [{"observation": "INJECT_LOAD_SHELL"}], schemas)
    messages = _messages(client)
    observed = messages[0]["content"]
    assert "SAFE" in observed and "OBSERVED_HEURISTIC" in observed and "OBSERVED_NEGATIVE" in observed
    assert "INITIAL_MODEL" not in observed and "INJECT_LOAD_SHELL" not in observed
    assert "INJECT_LOAD_SHELL" in messages[1]["content"]
    receipt = planner.skill_policy_receipt()
    assert set(receipt["context_sha256"]) == {"initial", "observed"}
    assert all(len(value) == 64 for value in receipt["context_sha256"].values())
    assert all(0 < count <= 5500 for count in receipt["context_chars"].values())


@pytest.mark.parametrize(
    ("safety", "error"),
    [("", SkillAssetUnavailableError), ("X" * 5501, SkillContextBudgetExceeded)],
)
def test_staged_policy_fails_before_provider_call(tmp_path, safety, error):
    client = _client()
    with pytest.raises(error):
        OpenAIJsonPlanner(client, "test", skill_policy="staged-v1", skill_service=_service(tmp_path, safety=safety))
    client.chat.completions.create.assert_not_called()


def test_unknown_policy_refused():
    client = _client()
    with pytest.raises(ValueError, match="Unsupported skill policy"):
        OpenAIJsonPlanner(client, "test", skill_policy="arbitrary")
    client.chat.completions.create.assert_not_called()
