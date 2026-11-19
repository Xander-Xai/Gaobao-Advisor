"""Frozen synthetic task E2E: real HTTP, real SQL, independent oracle, refusal.

The outer test spawns an entirely new process with its own DB and admin token;
no cloud model, externally fetched dataset, user record or credential is used.
"""

from __future__ import annotations

import subprocess
import sys

_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

dbpath = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(dbpath)
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_RAG_KB"] = "true"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "temporary-demo-session-" + "0" * 32
os.environ["ADMIN_TOKEN_SECRET"] = "temporary-demo-admin-" + "1" * 32

from scripts.seed_demo_data import DEMO_YEAR, seed_database
from server.admin.auth import hash_password
PW = "demo-" + "fixture-" + str(DEMO_YEAR)
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": "demo-operator", "password_hash": hash_password(PW, iterations=10000), "role": "operator"},
    {"username": "demo-viewer", "password_hash": hash_password(PW, iterations=10000), "role": "viewer"},
])
seed_database(db_path=dbpath, reset=True)

from db.database import get_session
from db.models import School, Major, AdmissionScore, EnrollmentPlan
from fastapi.testclient import TestClient
from server.main import app

school_a = "北辰示例科技大学（合成）"
school_b = "沧澜示例农业大学（合成）"
major_name = "计算机科学与技术（合成）"
province = "示例省·甲"
profile = {
    "school_a": school_a,
    "school_b": school_b,
    "major": major_name,
    "province": province,
    "score": 550,
    "subject_type": "物理类",
    "year": DEMO_YEAR,
}
path = "/api/v1/admin/agent-experiments/decision-card"
sample_path = path + "/example"
with TestClient(app) as client:
    login = client.post("/api/v1/admin/auth/login", json={"username": "demo-operator", "password": PW})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}

    assert client.post(path, json=profile).status_code == 401
    viewer = client.post("/api/v1/admin/auth/login", json={"username": "demo-viewer", "password": PW})
    assert viewer.status_code == 200
    viewer_headers = {"Authorization": "Bearer " + viewer.json()["access_token"]}
    assert client.post(path, json=profile, headers=viewer_headers).status_code == 403

    sample = client.get(sample_path, headers=headers)
    assert sample.status_code == 200
    assert sample.json()["request"] == profile

    response = client.post(path, json=profile, headers=headers)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["status"] == "evidence_complete_historical_only", card
    assert card["planner"] == "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    assert card["execution"]["tool_calls"] == 5
    assert card["execution"]["model_steps"] == 6
    assert card["execution"]["prompt_tokens"] is None
    assert len(card["schools"]) == 2
    assert len(card["evidence_ids"]) == 5
    assert len(set(card["evidence_ids"])) == 5
    assert all(e.startswith("synthetic:") for e in card["evidence_ids"])
    assert "不得用于真实志愿填报" in card["disclaimer"]
    assert "历史最低分差不是录取概率" in " ".join(card["manual_checks"])

    # Independent ORM oracle: compare output to seeded DB rather than to
    # the Agent controller's own answer or canned expectations.
    db = get_session()
    try:
        major = db.query(Major).filter_by(name=major_name).one()
        for entry in card["schools"]:
            school = db.query(School).filter_by(name=entry["school_name"]).one()
            admission = (
                db.query(AdmissionScore).filter_by(
                    school_id=school.id, major_id=major.id, province=province, year=DEMO_YEAR
                ).one()
            )
            plan = (
                db.query(EnrollmentPlan).filter_by(
                    school_id=school.id, major_id=major.id, province=province, year=DEMO_YEAR
                ).one()
            )
            assert entry["historical_min_score"] == admission.min_score
            assert entry["historical_min_rank"] == admission.min_rank
            assert entry["historical_subject_type"] == admission.subject_type
            assert entry["historical_score_difference"] == 550 - admission.min_score
            assert entry["plan_count"] == plan.plan_count
            assert entry["plan_subject_requirement"] == plan.subject_requirement
            assert entry["subject_constraint"] == "compatible"
            assert len(entry["evidence_ids"]) == 2
    finally:
        db.close()

    # The neighboring synthetic school has neither a matching history row
    # nor a plan for this major: an incomplete comparison must not be shown.
    incomplete = {**profile, "school_b": "海岳示例师范大学（合成）"}
    refused = client.post(path, json=incomplete, headers=headers)
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "needs_review"
    assert refused.json()["schools"] == []
    assert refused.json()["evidence_ids"] == []
    assert refused.json()["missing_evidence"]

    # A subject-type mismatch cannot quietly be interpreted as eligible.
    wrong_subject = client.post(path, json={**profile, "subject_type": "历史类"}, headers=headers)
    assert wrong_subject.status_code == 200
    assert wrong_subject.json()["status"] == "needs_review"
    assert wrong_subject.json()["schools"] == []

    assert client.post(path, json={**profile, "school_b": "真实高校"}, headers=headers).status_code == 422
    assert client.post(path, json={**profile, "score": 800}, headers=headers).status_code == 422
    assert client.post(path, json={**profile, "year": DEMO_YEAR - 1}, headers=headers).status_code == 422

    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.post(path, json=profile, headers=headers).status_code == 404
    assert client.get(sample_path, headers=headers).status_code == 404
print(json.dumps({
    "suite": "synthetic_decision_card_real_sql_http_oracle",
    "status": "PASS",
    "fact_sources": 5,
    "tool_calls": 5,
    "model": "NOT_USED",
    "missing_data_abstain": True,
    "subject_mismatch_abstain": True,
}))
"""


def test_decision_card_real_http_sql_oracle_and_negative_controls(tmp_path):
    process = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path / "decision-card-demo.db")],
        capture_output=True,
        check=False,
        text=True,
        timeout=110,
    )
    assert process.returncode == 0, process.stderr[-2400:] + process.stdout[-900:]
    result = process.stdout
    assert '"status": "PASS"' in result
    assert '"fact_sources": 5' in result
    assert '"model": "NOT_USED"' in result
