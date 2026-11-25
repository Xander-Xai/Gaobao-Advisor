"""Session-bound Chat requests reuse the single approved admissions journal."""

import hashlib
from dataclasses import asdict

import pytest

from db.models import Major, School
from server.agent.admissions_research_taskrun import digest, execute_approved, open_configured_journal
from server.auth import create_session_token
from tests.conftest import auth_header, login


@pytest.fixture
def research_env(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_DATASET_REF", "approved-catalog-v1")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_JOURNAL", str(tmp_path / "research.sqlite"))
    monkeypatch.setenv("ADMISSIONS_RESEARCH_MODEL_ID", "openai:approved-model")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_IMAGE_ID", "sha256:" + "a" * 64)
    monkeypatch.setenv("ADMISSIONS_RESEARCH_APPROVAL_REF", "approval-policy-2026-001")


def _request(session_id="session-owner-1234"):
    return {
        "session_id": session_id,
        "idempotency_key": "chat-research-001",
        "scope": {
            "school_ids": [101, 102],
            "major_id": 42,
            "province": "广东",
            "subject_type": "物理",
            "batch": "本科",
            "year_from": 2022,
            "year_to": 2024,
        },
    }


def _headers(session_id="session-owner-1234"):
    return {"Authorization": f"Bearer {create_session_token(session_id)}"}


def test_chat_research_request_is_session_owned_idempotent_and_waits_for_approval(admin_client, research_env):
    response = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers())

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "private, no-store"
    created = response.json()
    assert created["status"] == "PENDING_APPROVAL"
    assert created["spec"]["dataset_ref"] == "approved-catalog-v1"
    assert created["spec"]["model_id"] == "openai:approved-model"
    assert created["spec"]["approval_ref"] is None
    assert created["owner"] == "session_" + hashlib.sha256(b"session-owner-1234").hexdigest()
    assert "session-owner-1234" not in response.text

    duplicate = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers())
    assert duplicate.status_code == 201
    assert duplicate.json()["run_id"] == created["run_id"]

    changed = _request()
    changed["scope"]["year_to"] = 2023
    conflict = admin_client.post("/api/v1/chat/research-runs", json=changed, headers=_headers())
    assert conflict.status_code == 409

    status = admin_client.get(
        f"/api/v1/chat/research-runs/{created['run_id']}",
        params={"session_id": "session-owner-1234"},
        headers=_headers(),
    )
    assert status.status_code == 200
    assert status.json()["status"] == "PENDING_APPROVAL"


def test_chat_research_request_requires_session_proof_and_hides_other_sessions(admin_client, research_env):
    missing = admin_client.post("/api/v1/chat/research-runs", json=_request())
    assert missing.status_code == 401

    created = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers())
    run_id = created.json()["run_id"]
    other_session = "session-other-1234"
    hidden = admin_client.get(
        f"/api/v1/chat/research-runs/{run_id}",
        params={"session_id": other_session},
        headers=_headers(other_session),
    )
    assert hidden.status_code == 404


def test_chat_research_request_is_disabled_by_default_gate(admin_client, research_env, monkeypatch):
    monkeypatch.setenv("ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED", "false")
    response = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers())
    assert response.status_code == 404


def test_operator_can_review_session_submissions_without_session_credentials(admin_client, research_env):
    created = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers())
    assert created.status_code == 201, created.text

    operator = auth_header(login(admin_client, "operator"))
    queued = admin_client.get("/api/v1/admin/admissions-research/runs?state=PENDING_APPROVAL", headers=operator)

    assert queued.status_code == 200, queued.text
    assert [run["run_id"] for run in queued.json()] == [created.json()["run_id"]]

    viewer = auth_header(login(admin_client, "viewer"))
    denied = admin_client.get("/api/v1/admin/admissions-research/runs", headers=viewer)
    assert denied.status_code == 403


def test_chat_research_sse_emits_real_terminal_state(admin_client, research_env):
    created = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers()).json()
    journal = open_configured_journal()
    journal.approve(created["run_id"], "independent-operator", created["spec_sha256"])
    _, lease = journal.claim(created["run_id"], "approved-catalog-v1")
    journal.finish(created["run_id"], lease, None, "research_execution_failed")

    response = admin_client.get(
        f"/api/v1/chat/research-runs/{created['run_id']}/events",
        params={"session_id": "session-owner-1234"},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"status": "FAILED"' in response.text
    assert '"stage": "execution_failed"' in response.text
    assert '"terminal": true' in response.text


def test_chat_research_options_expose_searchable_authorized_catalog_ids(admin_client, admin_factory, research_env):
    with admin_factory() as db:
        first = School(name="Authorized Research School A", province="广东")
        second = School(name="Authorized Research School B", province="广东")
        synthetic = School(name="Hidden Synthetic Research School", province="广东", special_type="SYNTHETIC_DEMO_DATA")
        major = Major(name="Authorized Research Major", category="工学")
        db.add_all([first, second, synthetic, major])
        db.commit()
        first_id, second_id, major_id = first.id, second.id, major.id

    response = admin_client.get(
        "/api/v1/chat/research-runs/options",
        params={"session_id": "session-owner-1234", "school_query": "Authorized Research", "major_query": "Authorized"},
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    options = response.json()
    assert {item["id"] for item in options["schools"]} == {first_id, second_id}
    assert all(item["name"] != "Hidden Synthetic Research School" for item in options["schools"])
    assert options["majors"] == [{"id": major_id, "name": "Authorized Research Major"}]


def test_chat_request_approval_worker_and_private_report_use_one_journal(admin_client, research_env):
    from server.agent.admissions_analysis_sandbox import _expected
    from server.agent.admissions_deepagents import _coverage

    created = admin_client.post("/api/v1/chat/research-runs", json=_request(), headers=_headers()).json()
    run_id = created["run_id"]
    operator = auth_header(login(admin_client, "operator"))
    admin = auth_header(login(admin_client, "admin"))
    queue = admin_client.get("/api/v1/admin/admissions-research/runs?state=PENDING_APPROVAL", headers=operator)
    assert [item["run_id"] for item in queue.json()] == [run_id]

    approved = admin_client.post(
        f"/api/v1/admin/admissions-research/runs/{run_id}/approve",
        json={"spec_sha256": created["spec_sha256"]},
        headers=admin,
    )
    assert approved.status_code == 200, approved.text
    journal = open_configured_journal()

    def runner(scope, session_factory, model_id, **kwargs):
        kwargs["lease_guard"]()
        rows = [
            {
                "id": 17,
                "school_id": 101,
                "major_id": 42,
                "year": 2022,
                "min_score": 600,
                "max_score": 620,
                "min_rank": 12000,
            }
        ]
        evidence = {"admission-history": rows, "enrollment-plans": []}
        analysis = _expected(rows, ("school_id", "year"))
        task_id = "research_" + "b" * 32
        return {
            "schema": "admissions-research/v1",
            "engine": "deepagents",
            "task_id": task_id,
            "status": "VERIFIED_PARTIAL_RESEARCH",
            "scope": asdict(scope),
            "approval_ref_sha256": digest("approval-policy-2026-001"),
            "admission_recommendation_verified": False,
            "evidence": evidence,
            "evidence_sha256": digest(evidence),
            "sandbox_analysis": {
                "status": "VERIFIED_DESCRIPTIVE_ANALYSIS",
                "result": analysis,
                "result_sha256": digest(analysis),
                "cleanup_verified": True,
                "image_id": "sha256:" + "a" * 64,
                "input_sha256": digest({"rows": rows, "group_by": ["school_id", "year"]}),
            },
            "coverage": {kind: _coverage(records, scope, kind) for kind, records in evidence.items()},
            "receipt_ids": {
                kind: digest({"task": task_id, "scope": asdict(scope), "kind": kind, "rows": records})
                for kind, records in evidence.items()
            },
            "provider_usage": None,
        }

    result = execute_approved(journal, run_id, "approved-catalog-v1", None, runner=runner)
    assert result["status"] == "NEEDS_REVIEW"

    report = admin_client.get(
        f"/api/v1/chat/research-runs/{run_id}/report",
        params={"session_id": "session-owner-1234"},
        headers=_headers(),
    )
    assert report.status_code == 200, report.text
    assert "历史分数与位次统计" in report.text
    assert "12000" in report.text
    assert "approval-policy-2026-001" not in report.text
    assert "private, no-store" in report.headers["cache-control"]

    other_session = "session-other-1234"
    denied = admin_client.get(
        f"/api/v1/chat/research-runs/{run_id}/report",
        params={"session_id": other_session},
        headers=_headers(other_session),
    )
    assert denied.status_code == 404
