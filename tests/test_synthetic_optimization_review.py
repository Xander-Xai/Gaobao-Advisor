"""No-network guardrails for development-only optimization and approval simulation."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from benchmarks.synthetic_optimization_review import build_optimization_review
from tests.test_repeated_model_experiment import pairs


def test_failure_diagnostics_read_development_only():
    reports = pairs(2)
    reports[0][0]["results"][4]["passed"] = False
    reports[0][0]["results"][4]["failure_codes"] = ["factual_mismatch"]
    reports[0][0]["passed"] = 7
    reports[0][0]["results"][0]["failure_codes"] = ["holdout_secret_should_not_appear"]
    analysis = build_optimization_review(reports)
    assert analysis["evaluation_gate"] == "REVIEW_REQUIRED"
    assert analysis["development_failure_clusters"]["baseline"] == {"factual_mismatch": 1}
    assert analysis["development_failure_clusters"]["evidence_first"] == {}
    assert len(analysis["development_only_untested_hypotheses"]) == 1
    assert analysis["development_only_untested_hypotheses"][0]["state"] == "UNTESTED"
    assert "holdout_secret" not in json.dumps(analysis)
    assert analysis["model_provenance_verified"] is False
    assert analysis["production_deployment_authorized"] is False
    assert analysis["independent_human_ground_truth_verified"] is False


def test_holdout_regression_rejects_approval_even_if_candidate_uses_fewer_tokens():
    reports = pairs()
    reports[0][1]["results"][0]["passed"] = False
    reports[0][1]["results"][0]["failure_codes"] = ["outcome_mismatch"]
    reports[0][1]["passed"] = 7
    analysis = build_optimization_review(reports)
    assert analysis["evaluation_gate"] == "REJECT_CANDIDATE"
    assert analysis["regressed_case_ids"] == ["trial-1:case-0"]
    assert analysis["development_failure_clusters"]["evidence_first"] == {}


@pytest.mark.parametrize("kind", ["fake_controller", "reused_run", "schema", "bad_failure", "too_large"])
def test_bad_or_misrepresented_reports_are_rejected(kind):
    reports = pairs()
    if kind == "fake_controller":
        reports[0][0]["controller"] = "SCRIPTED_NOT_LLM"
    elif kind == "reused_run":
        reports[1][0]["run_id"] = reports[0][0]["run_id"]
    elif kind == "schema":
        reports[0][0]["provider_prompt_tokens"] = -1
    elif kind == "bad_failure":
        reports[0][0]["results"][4]["failure_codes"] = ["reveal secret!" * 50]
    else:
        reports[0][0]["unused_raw_prompt"] = "x" * 165000
    with pytest.raises(ValueError):
        build_optimization_review(reports)


def test_analysis_digest_changes_with_actual_report_changes():
    report = pairs()
    orig = build_optimization_review(report)
    modified = copy.deepcopy(report)
    modified[0][0]["results"][4]["elapsed_ms"] += 1
    changed = build_optimization_review(modified)
    assert orig["evidence_bundle_sha256"] != changed["evidence_bundle_sha256"]
    assert orig["model_id"] == changed["model_id"]


_E2E = r"""
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "synthetic_admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "optimizer-journal.sqlite")
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["APP_ENV"] = "development"
os.environ["PUBLIC_API_RATE_PER_SECOND"] = "1000"
os.environ["PUBLIC_API_RATE_BURST"] = "1000"
os.environ["SESSION_SECRET"] = "synthetic-optimization-test-session-" + "s" * 40
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-optimization-test-admin-" + "t" * 40

from server.admin.auth import hash_password
login_password = "synthetic-e2e-password"
password_hash = hash_password(login_password, iterations=10000)
accounts = [
    ("proposal-author", "admin"),
    ("reviewer-bravo", "admin"),
    ("reviewer-charlie", "admin"),
    ("stage-delta", "admin"),
    ("limited-operator", "operator"),
    ("read-only-viewer", "viewer"),
]
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": username, "password_hash": password_hash, "role": role}
    for username, role in accounts
])

from fastapi.testclient import TestClient
from server.main import app
from tests.test_repeated_model_experiment import pairs

base = "/api/v1/admin/agent-optimizer"

with TestClient(app) as client:
    def login(username):
        r = client.post(
            "/api/v1/admin/auth/login",
            json={"username": username, "password": login_password},
        )
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    author = login("proposal-author")
    b = login("reviewer-bravo")
    c = login("reviewer-charlie")
    d = login("stage-delta")
    operator = login("limited-operator")
    viewer = login("read-only-viewer")
    reports = pairs(2)
    reports[0][0]["results"][4]["passed"] = False
    reports[0][0]["results"][4]["failure_codes"] = ["factual_mismatch"]
    reports[0][0]["passed"] = 7

    assert client.post(base + "/proposals", json={"pairs": reports}).status_code == 401
    assert client.post(base + "/proposals", json={"pairs": reports}, headers=operator).status_code == 403
    assert client.get(base + "/demo-pointer", headers=viewer).status_code == 403
    assert client.post(base + "/proposals", json={"pairs": reports, "command": "run"}, headers=author).status_code == 422
    created = client.post(base + "/proposals", json={"pairs": reports}, headers=author)
    assert created.status_code == 201, created.text
    proposal = created.json()
    pid = proposal["proposal_id"]
    assert pid.startswith("opt_")
    assert proposal["status"] == "PENDING_ADMIN_ATTESTATION"
    assert proposal["report"]["evaluation_gate"] == "REVIEW_REQUIRED"
    assert proposal["report"]["development_failure_clusters"]["baseline"] == {"factual_mismatch": 1}
    assert proposal["report"]["holdout_statistical_significance"] == "NOT_ESTABLISHED"
    assert proposal["source_report_independently_verified"] is False
    assert proposal["production_deployment_authorized"] is False

    duplicate = client.post(base + "/proposals", json={"pairs": reports}, headers=author)
    assert duplicate.status_code == 201
    assert duplicate.json()["proposal_id"] == pid
    assert duplicate.json()["audit_events"] == proposal["audit_events"]

    route = base + "/proposals/" + pid
    yes = {"verdict": "attest_reviewed"}
    assert client.get(route, headers=operator).status_code == 403
    assert client.get(base + "/proposals/opt_" + "f" * 32, headers=d).status_code == 404
    assert client.post(base + "/proposals/opt_invalid/review", json=yes, headers=b).status_code == 404
    assert client.post(route + "/review", headers=author, json=yes).status_code == 409
    assert client.post(route + "/simulate", headers=d).status_code == 409
    first = client.post(route + "/review", headers=b, json=yes)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PENDING_ADMIN_ATTESTATION"
    assert len(first.json()["signoffs"]) == 1
    assert client.post(route + "/review", headers=b, json=yes).json()["signoffs"] == first.json()["signoffs"]
    assert client.post(route + "/review", headers=b, json={"verdict": "reject"}).status_code == 409
    second = client.post(route + "/review", headers=c, json=yes)
    assert second.status_code == 200 and second.json()["status"] == "TWO_ADMIN_ATTESTATIONS_UNVERIFIED"
    assert len(second.json()["signoffs"]) == 2
    assert client.post(route + "/simulate", headers=b).status_code == 409
    assert client.post(route + "/simulate", headers=author).status_code == 409
    simulated = client.post(route + "/simulate", headers=d)
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["status"] == "DEMO_POINTER_ACTIVE"
    pointer = client.get(base + "/demo-pointer", headers=d)
    assert pointer.status_code == 200
    assert pointer.json()["active_demo_pointer"] == pid
    assert pointer.json()["pointer_version"] == 1
    assert pointer.json()["effective_public_chat_prompt"] == "UNCHANGED"
    assert pointer.json()["real_canary_or_gray_rollout_executed"] is False

    assert client.post(route + "/simulate", headers=d).status_code == 409
    reverted = client.post(base + "/demo-pointer/rollback", headers=d)
    assert reverted.status_code == 200
    assert reverted.json()["active_demo_pointer"] is None
    assert reverted.json()["pointer_version"] == 2
    assert client.post(base + "/demo-pointer/rollback", headers=d).status_code == 409
    closed = client.get(route, headers=d).json()
    assert closed["status"] == "DEMO_POINTER_ROLLED_BACK"
    assert closed["audit_events"][-1]["kind"] == "SIMULATED_POINTER_ROLLBACK"
    assert client.post(route + "/simulate", headers=d).status_code == 409

    # Holdout degradation cannot be authorized by any number of admins.
    regressed = pairs(2)
    regressed[0][1]["results"][0]["passed"] = False
    regressed[0][1]["passed"] = 7
    rejected = client.post(base + "/proposals", json={"pairs": regressed}, headers=author)
    assert rejected.status_code == 201
    blocked = rejected.json()
    assert blocked["status"] == "BLOCKED_BY_EVAL"
    assert blocked["report"]["evaluation_gate"] == "REJECT_CANDIDATE"
    assert client.post(base + "/proposals/" + blocked["proposal_id"] + "/review", headers=b, json=yes).status_code == 409
    assert client.post(base + "/proposals/" + blocked["proposal_id"] + "/simulate", headers=d).status_code == 409

    # Review rejection cannot be revised or staged.
    alternative = pairs(2)
    alternative[0][0]["results"][5]["failure_codes"] = ["incomplete_evidence"]
    alternative[0][0]["results"][5]["passed"] = False
    alternative[0][0]["passed"] = 7
    bad = client.post(base + "/proposals", json={"pairs": alternative}, headers=author)
    assert bad.status_code == 201
    denied = base + "/proposals/" + bad.json()["proposal_id"]
    refusal = client.post(denied + "/review", headers=b, json={"verdict": "reject"})
    assert refusal.status_code == 200 and refusal.json()["status"] == "REJECTED_BY_ADMIN"
    assert client.post(denied + "/review", headers=c, json=yes).status_code == 409
    assert client.post(denied + "/simulate", headers=d).status_code == 409

    script = pairs(2)
    script[0][0]["controller"] = "SCRIPTED_NOT_LLM"
    assert client.post(base + "/proposals", json={"pairs": script}, headers=author).status_code == 422
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "false"
    assert client.get(base + "/demo-pointer", headers=d).status_code == 404
    os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
    os.environ["APP_ENV"] = "production"
    assert client.get(base + "/demo-pointer", headers=d).status_code == 404

print("synthetic_optimizer_approval_and_rollback_http_e2e=PASS")
"""


def test_authenticated_optimization_review_and_demo_pointer_e2e(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _E2E, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-5000:] + result.stdout[-1200:]
    assert "synthetic_optimizer_approval_and_rollback_http_e2e=PASS" in result.stdout
