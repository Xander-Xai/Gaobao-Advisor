"""Real FastAPI owner cancellation of an opt-in supervised worker process.

No provider call: normal case uses frozen fictional SQL, cancel case uses a
blocked fake process that claims the same TaskRun journal and spawns a child.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process group signals required")

_SCRIPT = r"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "admissions.sqlite")
os.environ["SYNTHETIC_TASKRUN_JOURNAL"] = str(root / "journal.sqlite")
os.environ["SYNTHETIC_TASKRUN_USE_PROCESS_SUPERVISOR"] = "true"
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "synthetic-supervisor-http-session-" + "x" * 40
os.environ["ADMIN_TOKEN_SECRET"] = "synthetic-supervisor-http-admin-" + "x" * 40

from server.admin.auth import hash_password
pw = "not-a-provider-key-supervisor-2026"
hashed = hash_password(pw, iterations=10000)
os.environ["ADMIN_USERS_JSON"] = json.dumps([
    {"username": "process-operator", "password_hash": hashed, "role": "operator"},
    {"username": "process-checker", "password_hash": hashed, "role": "admin"},
])
from scripts.seed_demo_data import seed_database, DEMO_SCHOOLS, DEMO_MAJORS, DEMO_YEAR
seed_database(db_path=root / "admissions.sqlite", reset=True)
from fastapi.testclient import TestClient
from server.main import app
from server.agent import taskrun_supervisor

payload = {
    "profile": {
        "school_a": DEMO_SCHOOLS[0][0],
        "school_b": DEMO_SCHOOLS[6][0],
        "major": DEMO_MAJORS[0][0],
        "province": "示例省·甲",
        "score": 550,
        "subject_type": "物理类",
        "year": DEMO_YEAR,
    },
    "engine": "deterministic",
}
route = "/api/v1/admin/agent-taskruns"
with TestClient(app) as client:
    def login(name):
        r = client.post("/api/v1/admin/auth/login", json={"username":name,"password":pw})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    owner, admin = login("process-operator"), login("process-checker")
    def new_run(key):
        create = client.post(route + "/runs", json={**payload,"idempotency_key":key}, headers=owner)
        assert create.status_code == 201, create.text
        task_id = create.json()["run_id"]
        approve = client.post(f"{route}/runs/{task_id}/approve", headers=admin)
        assert approve.status_code == 200, approve.text
        return task_id

    first = new_run("normal-supervisor-http-2026")
    done = client.post(f"{route}/runs/{first}/execute", headers=owner)
    assert done.status_code == 200, done.text
    assert done.json()["status"] in {"SUCCEEDED", "NEEDS_REVIEW"}
    assert done.json()["attempts"] == 1
    assert done.json()["result"] is not None
    assert done.json()["engine"] == "deterministic"

    second = new_run("cancel-supervisor-http-2026")
    ready = root / "http-worker-claimed.txt"
    orphan = root / "http-orphan-grandchild.txt"
    fake_worker = r'''
import subprocess,sys,time
from pathlib import Path
from server.agent.taskrun_store import SyntheticTaskRunStore
store = SyntheticTaskRunStore(Path(sys.argv[1]))
store.claim(sys.argv[2], lease_seconds=30)
subprocess.Popen([sys.executable, "-c",
    "import time,sys;from pathlib import Path;time.sleep(0.9);Path(sys.argv[1]).write_text('ORPHAN')",
    sys.argv[4]])
Path(sys.argv[3]).write_text("RUNNING")
time.sleep(40)
'''
    def fake_command(store, run_id, model, permit):
        assert run_id == second
        assert model is None and permit is False
        return [sys.executable, "-c", fake_worker, str(store.path), run_id, str(ready), str(orphan)]
    taskrun_supervisor._fixed_worker_command = fake_command
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"{route}/runs/{second}/execute", headers=owner)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "Worker did not start and claim task"
        cancel = client.post(f"{route}/runs/{second}/cancel", headers=owner)
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "CANCELLED"
        done = future.result(timeout=8)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "CANCELLED"
        assert done.json()["result"] is None
        assert done.json()["attempts"] == 1
    time.sleep(1.05)
    assert not orphan.exists(), "HTTP cancel failed to kill local child group"

    # Production always closes both supervisor execution and cancel APIs.
    os.environ["APP_ENV"] = "production"
    assert client.post(f"{route}/runs/{second}/execute", headers=owner).status_code == 404
    assert client.post(f"{route}/runs/{second}/cancel", headers=owner).status_code == 404
print("synthetic_supervised_http_cancel=2/2")
"""


def test_http_opt_in_supervisor_cancel_kills_local_process_tree(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=80,
    )
    assert result.returncode == 0, result.stderr[-3500:] + result.stdout[-900:]
    assert "synthetic_supervised_http_cancel=2/2" in result.stdout
