"""The experimental Agent route requires admin auth and explicit local opt-in."""

from __future__ import annotations

from tests.conftest import auth_header, login

SCHOOL_A = "北辰示例科技大学（合成）"
SCHOOL_B = "海岳示例师范大学（合成）"
MAJOR = "计算机科学与技术（合成）"
PATH = "/api/v1/admin/agent-experiments/research"


def _payload():
    return {"school_a": SCHOOL_A, "school_b": SCHOOL_B, "major": MAJOR}


def test_experiment_is_disabled_by_default(admin_client, monkeypatch):
    monkeypatch.delenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", raising=False)
    token = login(admin_client, "operator")
    response = admin_client.post(PATH, json=_payload(), headers=auth_header(token))
    assert response.status_code == 404
    assert "Experiment not enabled" in str(response.json())


def test_unauthenticated_and_viewer_cannot_execute(admin_client, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    assert admin_client.post(PATH, json=_payload()).status_code == 401
    viewer = login(admin_client, "viewer")
    assert admin_client.post(PATH, json=_payload(), headers=auth_header(viewer)).status_code == 403


def test_explicitly_enabled_route_rejects_real_or_duplicate_entities(admin_client, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    token = login(admin_client, "operator")
    for invalid in [
        {"school_a": "清华大学", "school_b": SCHOOL_B, "major": MAJOR},
        {"school_a": SCHOOL_A, "school_b": SCHOOL_A, "major": MAJOR},
        {"school_a": SCHOOL_A, "school_b": SCHOOL_B, "major": "真实专业"},
    ]:
        response = admin_client.post(PATH, json=invalid, headers=auth_header(token))
        assert response.status_code == 422


def test_production_mode_refuses_experiment_even_if_flag_enabled(admin_client, monkeypatch):
    token = login(admin_client, "operator")
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("APP_ENV", "production")
    response = admin_client.post(PATH, json=_payload(), headers=auth_header(token))
    assert response.status_code == 404


def test_authorized_operator_reaches_actual_harness_adapter(admin_client, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("APP_ENV", "development")
    captured = []

    def mock_research(a, b, major):
        captured.append((a, b, major))
        return {
            "status": "completed",
            "reason": "evidence_bound_finish",
            "planner": "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM",
            "facts": [],
            "disclaimer": "SYNTHETIC_DEMO",
            "execution": {"model_steps": 4, "tool_calls": 3},
        }

    monkeypatch.setattr("server.agent.synthetic_research.research_synthetic_facts", mock_research)
    token = login(admin_client, "operator")
    response = admin_client.post(PATH, json=_payload(), headers=auth_header(token))
    assert response.status_code == 200, response.text
    assert captured == [(SCHOOL_A, SCHOOL_B, MAJOR)]
    assert response.json()["planner"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"


def test_operator_can_discover_reproducible_synthetic_example(admin_client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    token = login(admin_client, "operator")
    response = admin_client.get(
        "/api/v1/admin/agent-experiments/example",
        headers=auth_header(token),
    )
    assert response.status_code == 200
    assert response.json()["data_profile"] == "SYNTHETIC_DEMO"
    assert response.json()["request"] == _payload()
