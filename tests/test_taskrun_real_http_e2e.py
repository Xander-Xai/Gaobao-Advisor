"""Fresh process: HTTP → two principals → TaskRun → worker → real SQL evidence."""

from __future__ import annotations

import subprocess
import sys

_PROCESS = r"""
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
dbpath = root / "admissions.db"
journal = root / "run-journal.db"
os.environ["DATABASE_URL"] = "sqlite:///" + str(dbpath)
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(journal)
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_RAG_KB"] = "true"
os.environ["SESSION_SECRET"] = "synthetic-session-" + "0" * 48
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-admin-secret-" + "1" * 48

from scripts.seed_demo_data import DEMO_YEAR, seed_database
from server.admin.auth import hash_password
pw = "synthetic-" + "exercise-" + str(DEMO_YEAR)
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": "demo-operator", "password_hash": hash_password(pw, iterations=10000), "role": "operator"},
    {"username": "demo-reviewer", "password_hash": hash_password(pw, iterations=10000), "role": "admin"},
])
seed_database(db_path=dbpath, reset=True)
from fastapi.testclient import TestClient
from server.main import app

url = "/api/v1/admin/agent-taskruns"
profile = {
    "school_a": "北辰示例科技大学（合成）",
    "school_b": "沧澜示例农业大学（合成）",
    "major": "计算机科学与技术（合成）",
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
}
with TestClient(app) as client:
    operator = client.post("/api/v1/admin/auth/login", json={"username": "demo-operator", "password": pw})
    reviewer = client.post("/api/v1/admin/auth/login", json={"username": "demo-reviewer", "password": pw})
    assert operator.status_code == reviewer.status_code == 200
    op = {"Authorization": "Bearer " + operator.json()["access_token"]}
    ad = {"Authorization": "Bearer " + reviewer.json()["access_token"]}
    request = {"engine": "deterministic", "idempotency_key": "real-http-flow-2026", "profile": profile}
    created = client.post(url + "/runs", json=request, headers=op)
    assert created.status_code == 201, created.text
    run = created.json()["run_id"]
    assert client.post(url + f"/runs/{run}/execute", headers=op).status_code == 409
    assert client.post(url + f"/runs/{run}/approve", headers=op).status_code == 403
    assert client.post(url + f"/runs/{run}/approve", headers=ad).status_code == 200
    completed = client.post(url + f"/runs/{run}/execute", headers=op)
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["status"] == "SUCCEEDED", result
    assert result["attempts"] == 1
    card = result["result"]
    assert card["status"] == "evidence_complete_historical_only"
    assert card["execution"]["tool_calls"] == 5
    assert len(card["evidence_ids"]) == 5
    assert len(card["schools"]) == 2
    assert card["planner"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert client.get(url + f"/runs/{run}", headers=op).json()["status"] == "SUCCEEDED"
    assert client.post(url + f"/runs/{run}/execute", headers=op).status_code == 409

    denied_profile = dict(profile)
    denied_profile["school_b"] = "海岳示例师范大学（合成）"
    rejected = client.post(
        url + "/runs",
        json={"engine": "deterministic", "idempotency_key": "missing-sql-flow-2026", "profile": denied_profile},
        headers=op,
    )
    assert rejected.status_code == 201
    negative_id = rejected.json()["run_id"]
    assert client.post(url + f"/runs/{negative_id}/approve", headers=ad).status_code == 200
    negative = client.post(url + f"/runs/{negative_id}/execute", headers=op)
    assert negative.status_code == 200
    assert negative.json()["status"] == "NEEDS_REVIEW"
    assert negative.json()["result"]["schools"] == []
    assert negative.json()["result"]["missing_evidence"]

    # A human approval alone never enables paid external models from HTTP.
    model_item = client.post(
        url + "/runs",
        json={"engine": "deepagents", "idempotency_key": "model-gate-only-2026", "profile": profile},
        headers=op,
    )
    assert model_item.status_code == 201
    model_id = model_item.json()["run_id"]
    assert client.post(url + f"/runs/{model_id}/approve", headers=ad).status_code == 200
    assert client.post(url + f"/runs/{model_id}/execute", headers=op).status_code == 409

    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.post(url + "/runs", json=request, headers=op).status_code == 404
print(json.dumps({
    "status": "PASS", "flow": "REAL_HTTP_TWO_PERSON_APPROVAL_AND_SQL",
    "approved_success": True, "negative_needs_review": True,
    "model_via_web_blocked": True, "tool_calls": 5,
}))
"""


def test_full_http_taskrun_approval_worker_sql_and_model_browser_gate(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _PROCESS, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2400:] + result.stdout[-900:]
    assert '"status": "PASS"' in result.stdout
    assert '"tool_calls": 5' in result.stdout
