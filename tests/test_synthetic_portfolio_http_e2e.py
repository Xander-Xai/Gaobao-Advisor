"""Real FastAPI + auth + synthetic SQL portfolio integration in isolated subprocess."""

import os
import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

db = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(db)
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "synthetic-portfolio-session-secret-000000000000"
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-portfolio-admin-secret-00000000000000"
from server.admin.auth import hash_password
secret = "fictional-admin-testing-2026"
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": "portfolio-operator", "password_hash": hash_password(secret, iterations=10000), "role": "operator"},
    {"username": "portfolio-viewer", "password_hash": hash_password(secret, iterations=10000), "role": "viewer"},
])
from scripts.seed_demo_data import seed_database, DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
seed_database(db_path=db, reset=True)
from fastapi.testclient import TestClient
from server.main import app

route = "/api/v1/admin/agent-experiments/portfolio"
body = {
    "province": "示例省·甲",
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
    "candidates": [
        {"school": DEMO_SCHOOLS[0][0], "major": DEMO_MAJORS[0][0]},
        {"school": DEMO_SCHOOLS[6][0], "major": DEMO_MAJORS[0][0]},
        {"school": DEMO_SCHOOLS[2][0], "major": DEMO_MAJORS[4][0]},
        {"school": DEMO_SCHOOLS[4][0], "major": DEMO_MAJORS[8][0]},
        {"school": DEMO_SCHOOLS[1][0], "major": DEMO_MAJORS[0][0]},
    ],
}
with TestClient(app) as client:
    def headers(username):
        auth = client.post("/api/v1/admin/auth/login", json={"username": username, "password": secret})
        assert auth.status_code == 200, auth.text
        return {"Authorization": "Bearer " + auth.json()["access_token"]}
    operator = headers("portfolio-operator")
    viewer = headers("portfolio-viewer")
    assert client.post(route, json=body).status_code == 401
    assert client.post(route, json=body, headers=viewer).status_code == 403
    response = client.post(route, json=body, headers=operator)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["data_profile"] == "SYNTHETIC_DEMO"
    assert data["evaluated_count"] == 5
    assert data["eligible_history_count"] == 4
    assert data["excluded_count"] == 1
    assert data["sql_tool_calls"] == 13
    assert data["human_review_complete"] is False
    assert data["admission_probability"] is None
    assert data["application_ranking"] is None
    invalid = json.loads(json.dumps(body))
    invalid["candidates"][0]["school"] = "真实大学"
    assert client.post(route, json=invalid, headers=operator).status_code == 422
    invalid = json.loads(json.dumps(body))
    invalid["candidates"][0]["notes"] = "untrusted prompt"
    assert client.post(route, json=invalid, headers=operator).status_code == 422
    invalid = json.loads(json.dumps(body))
    invalid["candidates"] = invalid["candidates"][:2]
    assert client.post(route, json=invalid, headers=operator).status_code == 422
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.post(route, json=body, headers=operator).status_code == 404
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    os.environ["APP_ENV"] = "production"
    assert client.post(route, json=body, headers=operator).status_code == 404
print("synthetic_portfolio_http_verified=1/1")
"""


def test_authenticated_multi_candidate_sql_portfolio_api(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    run = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path / "portfolio.sqlite")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert run.returncode == 0, run.stderr[-3200:] + run.stdout[-900:]
    assert "synthetic_portfolio_http_verified=1/1" in run.stdout
