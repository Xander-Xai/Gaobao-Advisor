"""True FastAPI → business tools → bounded Harness E2E in a fresh process.

No cloud model, no fake tool handler, no existing user database or credentials.
"""

import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

database_path = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(database_path)
os.environ["APP_ENV"] = "development"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["SESSION_SECRET"] = "synthetic-test-session-secret-000000000000000000"
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-test-admin-token-secret-000000000000000000"
os.environ["ENABLE_RAG_KB"] = "true"

from server.admin.auth import hash_password
TEST_PW = "synthetic" + "-pass-" + "2026"  # deliberately fabricated, never a deployed credential
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {
        "username": "demo-operator",
        "password_hash": hash_password(TEST_PW, iterations=10000),
        "role": "operator",
    }
])
from scripts.seed_demo_data import seed_database
seed_database(db_path=database_path, reset=True)

from fastapi.testclient import TestClient
from server.main import app

with TestClient(app) as client:
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "demo-operator", "password": TEST_PW},
    )
    assert login.status_code == 200, f"Login failed: {login.status_code}"
    token = login.json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    payload = {
        "school_a": "北辰示例科技大学（合成）",
        "school_b": "海岳示例师范大学（合成）",
        "major": "计算机科学与技术（合成）",
    }
    route = "/api/v1/admin/agent-experiments/research"
    result = client.post(route, json=payload, headers=headers)
    assert result.status_code == 200, f"Agent failed: {result.status_code}"
    report = result.json()
    assert report["status"] == "completed", report.get("reason")
    assert report["planner"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert report["execution"]["tool_calls"] == 3
    assert report["execution"]["model_steps"] == 4
    assert len(report["facts"]) == 3
    assert report["facts"][0]["province"] == "示例省·甲"
    assert report["facts"][1]["province"] == "示例省·甲"
    assert report["facts"][2]["category"] == "工学"
    assert all(item["source_id"].startswith("synthetic:") for item in report["facts"])
    assert "真实高考" in report["disclaimer"]
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    disabled = client.post(route, json=payload, headers=headers)
    assert disabled.status_code == 404
print(json.dumps({"result": "PASS", "tool_calls": 3, "facts": 3, "profile": "SYNTHETIC_DEMO"}))
"""


def test_real_synthetic_api_tools_complete_in_isolated_subprocess(tmp_path):
    process = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path / "gaobao-real-api-synthetic.db")],
        check=False,
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert process.returncode == 0, process.stderr[-1800:]
    assert '"result": "PASS"' in process.stdout
    assert '"tool_calls": 3' in process.stdout
    assert '"facts": 3' in process.stdout
