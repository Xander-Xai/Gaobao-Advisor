"""TaskRun API authorization and maker-checker approval gates."""

from __future__ import annotations

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from tests.conftest import auth_header, login

PATH = "/api/v1/admin/agent-taskruns"


def body(engine="deterministic", key="sample-idempotency-2026"):
    return {
        "engine": engine,
        "idempotency_key": key,
        "profile": {
            "school_a": DEMO_SCHOOLS[0][0],
            "school_b": DEMO_SCHOOLS[6][0],
            "major": DEMO_MAJORS[0][0],
            "province": "示例省·甲",
            "score": 550,
            "subject_type": "物理类",
            "year": DEMO_YEAR,
        },
    }


def test_operator_creates_and_admin_approves_but_cannot_approve_own_task(
    admin_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("SYNTHETIC_TASKRUN_JOURNAL", str(tmp_path / "tasks.db"))
    operator = auth_header(login(admin_client, "operator"))
    administrator = auth_header(login(admin_client, "admin"))
    viewer = auth_header(login(admin_client, "viewer"))

    assert admin_client.post(f"{PATH}/runs", json=body()).status_code == 401
    assert admin_client.post(f"{PATH}/runs", json=body(), headers=viewer).status_code == 403
    created = admin_client.post(f"{PATH}/runs", json=body(), headers=operator)
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    assert created.json()["status"] == "PENDING_APPROVAL"
    assert admin_client.post(f"{PATH}/runs", json=body(), headers=operator).json()["run_id"] == run_id
    changed = body()
    changed["profile"]["score"] = 559
    assert admin_client.post(f"{PATH}/runs", json=changed, headers=operator).status_code == 409

    assert admin_client.post(f"{PATH}/runs/{run_id}/approve", headers=operator).status_code == 403
    assert admin_client.post(f"{PATH}/runs/{run_id}/execute", headers=operator).status_code == 409
    approved = admin_client.post(f"{PATH}/runs/{run_id}/approve", headers=administrator)
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "admin"
    assert admin_client.get(f"{PATH}/runs/{run_id}", headers=viewer).status_code == 403

    own = admin_client.post(
        f"{PATH}/runs", json=body(key="admin-self-request-2026"), headers=administrator
    )
    assert own.status_code == 201
    assert (
        admin_client.post(f"{PATH}/runs/{own.json()['run_id']}/approve", headers=administrator).status_code
        == 409
    )


def test_model_task_is_never_executable_from_browser_even_after_approval(
    admin_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("SYNTHETIC_TASKRUN_JOURNAL", str(tmp_path / "tasks.db"))
    operator = auth_header(login(admin_client, "operator"))
    administrator = auth_header(login(admin_client, "admin"))
    result = admin_client.post(f"{PATH}/runs", json=body("deepagents"), headers=operator)
    assert result.status_code == 201
    run_id = result.json()["run_id"]
    assert admin_client.post(f"{PATH}/runs/{run_id}/approve", headers=administrator).status_code == 200
    answer = admin_client.post(f"{PATH}/runs/{run_id}/execute", headers=operator)
    assert answer.status_code == 409
    assert "worker" in answer.text
    assert admin_client.get(f"{PATH}/runs/{run_id}", headers=operator).json()["status"] == "APPROVED"


def test_feature_disabled_cannot_create_even_authenticated(admin_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SYNTHETIC_TASKRUN_JOURNAL", str(tmp_path / "tasks.db"))
    monkeypatch.delenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", raising=False)
    operator = auth_header(login(admin_client, "operator"))
    assert admin_client.post(f"{PATH}/runs", json=body(), headers=operator).status_code == 404
    assert not (tmp_path / "tasks.db").exists()
