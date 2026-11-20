"""Authenticated synthetic evidence memory lifecycle: end-to-end, no provider calls.

This test uses fabricated admins and fictional SQL. Two admin accounts are not
two verified admissions experts; the memory feature cannot be advertised as
professional human review or real admissions recommendation.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "taskruns.sqlite")
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "governed-memory-test-session-" + "s" * 40
os.environ["ADMIN_TOKEN_SECRET"] = "governed-memory-test-admin-" + "t" * 40

from server.admin.auth import hash_password
password = "synthetic-governed-memory-password-2026"
encoded = hash_password(password, iterations=10000)
accounts = [
    ("owner-one", "operator"),
    ("owner-two", "operator"),
    ("execution-approver", "admin"),
    ("reviewer-one", "admin"),
    ("reviewer-two", "admin"),
    ("memory-publisher", "admin"),
    ("plain-viewer", "viewer"),
]
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": username, "password_hash": encoded, "role": role}
    for username, role in accounts
])

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
seed_database(db_path=root / "admissions.sqlite", reset=True)

from fastapi.testclient import TestClient
from server.main import app
from server.agent.governed_evidence_memory import SyntheticEvidenceMemoryStore
from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict, TaskRunNotFound

base = "/api/v1/admin/agent-taskruns"
portfolio = {
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
    "candidates": [
        {"school": DEMO_SCHOOLS[0][0], "major": DEMO_MAJORS[0][0]},
        {"school": DEMO_SCHOOLS[6][0], "major": DEMO_MAJORS[0][0]},
        {"school": DEMO_SCHOOLS[2][0], "major": DEMO_MAJORS[4][0]},
    ],
}

with TestClient(app) as client:
    def auth(username):
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200, login.text
        return {"Authorization": "Bearer " + login.json()["access_token"]}

    owner = auth("owner-one")
    other_owner = auth("owner-two")
    checker = auth("execution-approver")
    reviewer1 = auth("reviewer-one")
    reviewer2 = auth("reviewer-two")
    publisher = auth("memory-publisher")
    viewer = auth("plain-viewer")

    def completed(key):
        created = client.post(
            base + "/portfolio-runs",
            json={"portfolio": portfolio, "idempotency_key": key},
            headers=owner,
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        assert client.post(f"{base}/runs/{run_id}/approve", headers=checker).status_code == 200
        finished = client.post(f"{base}/runs/{run_id}/execute", headers=owner)
        assert finished.status_code == 200 and finished.json()["status"] == "SUCCEEDED", finished.text
        return run_id

    def sign_both(run_id):
        response = client.get(f"{base}/runs/{run_id}/review", headers=owner)
        assert response.status_code == 200, response.text
        digest = response.json()["receipt_sha256"]
        payload = {"verdict": "verified_historical_only", "receipt_sha256": digest}
        assert client.post(f"{base}/runs/{run_id}/review", headers=reviewer1, json=payload).status_code == 200
        assert client.post(f"{base}/runs/{run_id}/review", headers=reviewer2, json=payload).status_code == 200
        return digest

    run_id = completed("memory-demo-unique-one")
    endpoint = f"{base}/runs/{run_id}/memory"
    assert client.post(endpoint, json={"ttl_seconds": 3600}).status_code == 401
    assert client.post(endpoint, json={"ttl_seconds": 3600}, headers=viewer).status_code == 403
    assert client.post(endpoint, json={"ttl_seconds": 3600}, headers=owner).status_code == 403
    assert client.post(endpoint, json={"ttl_seconds": 3600}, headers=publisher).status_code == 409
    assert client.get(endpoint, headers=owner).status_code == 404
    assert client.post(endpoint, json={"ttl_seconds": 1}, headers=publisher).status_code == 422
    assert client.post(endpoint, json={"ttl_seconds": 86400 * 31}, headers=publisher).status_code == 422
    assert client.post(endpoint, json={"ttl_seconds": 120, "extra": "prompt"}, headers=publisher).status_code == 422

    first_digest = sign_both(run_id)
    created = client.post(endpoint, json={"ttl_seconds": 3600}, headers=publisher)
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["memory_kind"] == "REVIEWED_SYNTHETIC_PORTFOLIO_POINTER"
    assert record["data_profile"] == "SYNTHETIC_DEMO"
    assert record["source_run_id"] == run_id
    assert record["source_receipt_sha256"] == first_digest
    assert record["published_by"] == "memory-publisher"
    assert record["historical_comparisons"]
    assert record["verified_against_current_synthetic_sql"] is True
    assert record["independently_verified_expert_review"] is False
    assert record["automatic_model_memory_injection"] is False
    assert record["real_admissions_accuracy"] == "NOT_MEASURED"
    assert record["production_release_authorized"] is False
    assert "profile" not in record and "raw_model_output" not in record

    again = client.post(endpoint, json={"ttl_seconds": 86400}, headers=publisher)
    assert again.status_code == 201
    assert again.json()["expires_at"] == record["expires_at"]  # cannot extend silently

    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=viewer).status_code == 403
    assert client.get(endpoint, headers=other_owner).status_code == 404
    assert client.delete(endpoint, headers=other_owner).status_code == 404
    active = client.get(endpoint, headers=owner)
    assert active.status_code == 200 and active.json()["source_receipt_sha256"] == first_digest
    owner_list = client.get(base + "/evidence-memory", headers=owner)
    assert owner_list.status_code == 200 and owner_list.json()["active_count"] == 1
    assert owner_list.json()["items"][0]["source_run_id"] == run_id
    assert client.get(base + "/evidence-memory", headers=other_owner).json()["active_count"] == 0
    assert client.get(base + "/evidence-memory", headers=viewer).status_code == 403

    # A separate run for the *same* fixed portfolio must not overwrite source provenance.
    conflicting_id = completed("memory-demo-unique-two")
    sign_both(conflicting_id)
    conflicting_url = f"{base}/runs/{conflicting_id}/memory"
    conflict = client.post(conflicting_url, json={}, headers=publisher)
    assert conflict.status_code == 409
    assert client.get(endpoint, headers=owner).json()["source_run_id"] == run_id

    # A stored pointer is NOT frozen truth. If source SQL changes, reads must fail closed.
    from db.database import get_session
    from db.models import AdmissionScore, School, Major
    session = get_session()
    school = session.query(School).filter(School.name == DEMO_SCHOOLS[0][0]).one()
    major = session.query(Major).filter(Major.name == DEMO_MAJORS[0][0]).one()
    entry = session.query(AdmissionScore).filter(
        AdmissionScore.school_id == school.id,
        AdmissionScore.major_id == major.id,
        AdmissionScore.province == portfolio["province"],
        AdmissionScore.year == DEMO_YEAR,
    ).one()
    target_id = entry.id
    original = entry.min_score
    entry.min_score = original + 1
    session.commit()
    session.close()
    assert client.get(endpoint, headers=owner).status_code == 409
    listed = client.get(base + "/evidence-memory", headers=owner)
    assert listed.status_code == 200
    assert listed.json()["active_count"] == 0 and listed.json()["blocked_stale_count"] == 1

    session = get_session()
    entry = session.query(AdmissionScore).filter(AdmissionScore.id == target_id).one()
    entry.min_score = original
    session.commit()
    session.close()
    assert client.get(endpoint, headers=owner).status_code == 200

    # Database receipt tampering cannot be used to bypass a frozen review digest.
    with sqlite3.connect(root / "taskruns.sqlite") as db:
        original_receipt = db.execute(
            "SELECT result_json FROM synthetic_task_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        db.execute(
            "UPDATE synthetic_task_runs SET result_json=? WHERE run_id=?",
            (json.dumps({"data_profile": "SYNTHETIC_DEMO", "forged": True}), run_id),
        )
        db.commit()
    assert client.get(endpoint, headers=owner).status_code == 409
    with sqlite3.connect(root / "taskruns.sqlite") as db:
        db.execute(
            "UPDATE synthetic_task_runs SET result_json=? WHERE run_id=?",
            (original_receipt, run_id),
        )
        db.commit()
    assert client.get(endpoint, headers=owner).status_code == 200

    # Demonstrate actual expiry without wall-clock sleeping.
    with sqlite3.connect(root / "taskruns.sqlite") as db:
        db.execute("UPDATE synthetic_evidence_memory SET expires_at=0 WHERE source_run_id=?", (run_id,))
        db.commit()
    assert client.get(endpoint, headers=owner).status_code == 404
    assert client.get(base + "/evidence-memory", headers=owner).json()["active_count"] == 0
    assert client.post(endpoint, json={"ttl_seconds": 60}, headers=publisher).status_code == 201

    # Delete only the memory index. The TaskRun and immutable review still exist.
    deleted = client.delete(endpoint, headers=owner)
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert deleted.json()["source_taskrun_retained"] is True
    assert client.get(endpoint, headers=owner).status_code == 404
    assert client.delete(endpoint, headers=owner).status_code == 404
    assert client.get(f"{base}/runs/{run_id}", headers=owner).json()["status"] == "SUCCEEDED"
    assert client.get(f"{base}/runs/{run_id}/review", headers=owner).json()["signoff_count"] == 2
    # Reuse after deliberate forget is permitted; no copied facts survive in the memory table.
    assert client.post(conflicting_url, json={}, headers=publisher).status_code == 201

    # Review rejection cannot ever enter memory.
    rejected_id = completed("memory-demo-rejected-three")
    rejected_url = f"{base}/runs/{rejected_id}/memory"
    digest = client.get(f"{base}/runs/{rejected_id}/review", headers=owner).json()["receipt_sha256"]
    yes = {"verdict": "verified_historical_only", "receipt_sha256": digest}
    no = {"verdict": "reject", "receipt_sha256": digest}
    assert client.post(f"{base}/runs/{rejected_id}/review", json=yes, headers=reviewer1).status_code == 200
    assert client.post(f"{base}/runs/{rejected_id}/review", json=no, headers=reviewer2).status_code == 200
    assert client.post(rejected_url, json={}, headers=publisher).status_code == 409

    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.get(conflicting_url, headers=owner).status_code == 404
    assert client.post(conflicting_url, json={}, headers=publisher).status_code == 404
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    os.environ["APP_ENV"] = "production"
    assert client.get(conflicting_url, headers=owner).status_code == 404

print("governed_synthetic_memory_auth_provenance_ttl_conflict_delete_e2e=PASS")
"""


def test_authenticated_governed_synthetic_memory_lifecycle(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-5000:] + result.stdout[-1000:]
    assert "governed_synthetic_memory_auth_provenance_ttl_conflict_delete_e2e=PASS" in result.stdout
