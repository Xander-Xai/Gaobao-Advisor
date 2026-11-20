"""Authenticated, durable synthetic review declarations with separation of duties.

Creates three fabricated admin identities and one operator. No real reviewers,
admissions facts or LLM requests are involved. We test *ability* to collect
separate attestations, never claim actual expert review happened.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "taskruns.sqlite")
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "synthetic-review-test-session-secret-" + "x" * 40
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-review-test-token-secret-" + "x" * 40

from server.admin.auth import hash_password
password = "synthetic-admin-test-password-2026"
encoded = hash_password(password, iterations=10000)
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": "evidence-operator", "password_hash": encoded, "role": "operator"},
    {"username": "execution-checker", "password_hash": encoded, "role": "admin"},
    {"username": "reviewer-alpha", "password_hash": encoded, "role": "admin"},
    {"username": "reviewer-bravo", "password_hash": encoded, "role": "admin"},
    {"username": "reviewer-charlie", "password_hash": encoded, "role": "admin"},
    {"username": "evidence-viewer", "password_hash": encoded, "role": "viewer"},
])
from scripts.seed_demo_data import DEMO_SCHOOLS, DEMO_MAJORS, DEMO_YEAR, seed_database
seed_database(db_path=root / "admissions.sqlite", reset=True)
from fastapi.testclient import TestClient
from server.main import app
from server.agent.taskrun_store import SyntheticTaskRunStore

base = "/api/v1/admin/agent-taskruns"
store = SyntheticTaskRunStore(root / "taskruns.sqlite")
portfolio = {
    "province":"示例省·甲", "score":550, "subject_type":"物理类",
    "year":DEMO_YEAR,
    "candidates":[
        {"school":DEMO_SCHOOLS[0][0], "major":DEMO_MAJORS[0][0]},
        {"school":DEMO_SCHOOLS[6][0], "major":DEMO_MAJORS[0][0]},
        {"school":DEMO_SCHOOLS[2][0], "major":DEMO_MAJORS[4][0]},
    ]
}
with TestClient(app) as client:
    def auth(username):
        response = client.post("/api/v1/admin/auth/login", json={"username":username,"password":password})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}
    owner = auth("evidence-operator")
    approver = auth("execution-checker")
    alpha = auth("reviewer-alpha")
    bravo = auth("reviewer-bravo")
    charlie = auth("reviewer-charlie")
    viewer = auth("evidence-viewer")

    def create_completed(key):
        resp = client.post(
            base + "/portfolio-runs",
            json={"portfolio":portfolio, "idempotency_key":key},
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["run_id"]
        assert client.post(f"{base}/runs/{task_id}/approve", headers=approver).status_code == 200
        done = client.post(f"{base}/runs/{task_id}/execute", headers=owner)
        assert done.status_code == 200 and done.json()["status"] == "SUCCEEDED", done.text
        return task_id

    task_id = create_completed("synthetic-review-2026")
    url = f"{base}/runs/{task_id}/review"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=viewer).status_code == 403
    initial = client.get(url, headers=owner)
    assert initial.status_code == 200, initial.text
    initial = initial.json()
    sha = initial["receipt_sha256"]
    assert len(sha) == 64
    assert initial["review_state"] == "PENDING_REVIEW" and initial["signoff_count"] == 0
    assert initial["professional_policy_review_complete"] is False
    assert initial["real_admissions_ground_truth"] is False

    yes = {"verdict":"verified_historical_only", "receipt_sha256":sha}
    assert client.post(url, json=yes).status_code == 401
    assert client.post(url, json=yes, headers=owner).status_code == 403
    assert client.post(url, json=yes, headers=viewer).status_code == 403
    assert client.post(url, json=yes, headers=approver).status_code == 409
    assert client.post(url, json={"verdict":"verified_historical_only","receipt_sha256":"a"*64}, headers=alpha).status_code == 409
    assert client.post(url, json={**yes, "engine":"deepagents"}, headers=alpha).status_code == 422
    assert client.post(url, json={"verdict":"accept","receipt_sha256":sha}, headers=alpha).status_code == 422

    first = client.post(url, json=yes, headers=alpha)
    assert first.status_code == 200, first.text
    assert first.json()["review_state"] == "PENDING_SECOND_REVIEW"
    assert first.json()["signoff_count"] == 1
    assert client.post(url, json=yes, headers=alpha).json()["signoff_count"] == 1
    assert client.post(url, json={**yes, "verdict":"reject"}, headers=alpha).status_code == 409

    second = client.post(url, json=yes, headers=bravo)
    assert second.status_code == 200, second.text
    assert second.json()["review_state"] == "TWO_ADMIN_SIGNOFFS_SYNTHETIC_ONLY"
    assert second.json()["signoff_count"] == 2
    assert second.json()["real_admissions_ground_truth"] is False
    assert second.json()["production_release_authorized"] is False
    assert client.post(url, json=yes, headers=charlie).status_code == 409

    # Re-instantiating the store confirms durable signoffs and frozen receipt.
    reloaded = SyntheticTaskRunStore(root / "taskruns.sqlite")
    status = reloaded.get_portfolio_review(task_id, requester="evidence-operator")
    assert status["signoff_count"] == 2
    assert status["receipt_sha256"] == sha

    contested_id = create_completed("synthetic-review-disputed-2026")
    contested_url = f"{base}/runs/{contested_id}/review"
    contested_sha = client.get(contested_url, headers=owner).json()["receipt_sha256"]
    assert client.post(contested_url, json={"verdict":"verified_historical_only","receipt_sha256":contested_sha}, headers=alpha).status_code == 200
    dispute = client.post(contested_url, json={"verdict":"reject","receipt_sha256":contested_sha}, headers=bravo)
    assert dispute.status_code == 200
    assert dispute.json()["review_state"] == "REJECTED_SYNTHETIC_EVIDENCE"
    assert dispute.json()["signoff_count"] == 2

    # Competing signoff writers must not produce more than two rows.
    from concurrent.futures import ThreadPoolExecutor
    from server.agent.taskrun_store import TaskRunConflict
    competing_id = create_completed("synthetic-review-concurrent-2026")
    competing_sha = client.get(f"{base}/runs/{competing_id}/review", headers=owner).json()["receipt_sha256"]
    def sign_simultaneously(who):
        try:
            return SyntheticTaskRunStore(root / "taskruns.sqlite").signoff_portfolio_review(
                competing_id, reviewer=who,
                verdict="verified_historical_only", receipt_sha256=competing_sha,
            )["review_state"]
        except TaskRunConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(sign_simultaneously, ["reviewer-alpha", "reviewer-bravo", "reviewer-charlie"]))
    assert responses.count("CONFLICT") == 1, responses
    assert store.get_portfolio_review(competing_id, requester="evidence-operator")["signoff_count"] == 2

    pending = client.post(base + "/portfolio-runs", json={
        "portfolio":portfolio, "idempotency_key":"synthetic-review-not-done-2026"
    }, headers=owner)
    assert pending.status_code == 201
    pending_url = f"{base}/runs/{pending.json()['run_id']}/review"
    assert client.get(pending_url, headers=owner).status_code == 409
    assert client.post(pending_url, json=yes, headers=alpha).status_code == 409
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.get(url, headers=owner).status_code == 404
    assert client.post(url, json=yes, headers=alpha).status_code == 404
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    os.environ["APP_ENV"] = "production"
    assert client.post(url, json=yes, headers=alpha).status_code == 404
print("synthetic_review_authenticated_http_e2e=1/1")
"""


def test_auth_bound_immutable_dual_review_signoffs(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    run = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr[-3600:] + run.stdout[-800:]
    assert "synthetic_review_authenticated_http_e2e=1/1" in run.stdout
