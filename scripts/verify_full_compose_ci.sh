#!/usr/bin/env bash
# Full-stack Docker Compose CI acceptance: MySQL, Redis, API, worker, frontend, Nginx.
# Uses synthetic data and temporary credentials only. Never touches an existing .env.full.
set -Eeuo pipefail
umask 077

cd "$(dirname "$0")/.."
if [ -e .env.full ]; then
  echo "Refusing to overwrite an existing .env.full" >&2
  exit 2
fi

TMP="$(mktemp -d)"
LOG_DIR="${RUNNER_TEMP:-/tmp}/gaobao-full-compose-logs"
mkdir -p "$LOG_DIR"
export COMPOSE_PROJECT_NAME="gaobao-full-ci-${GITHUB_RUN_ID:-local}-$$"
COMPOSE_EXTRA_FILE="${FULL_COMPOSE_OVERRIDE:-}"

compose() {
  if [ -n "$COMPOSE_EXTRA_FILE" ]; then
    docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml \
      -f "$COMPOSE_EXTRA_FILE" --profile full "$@"
  else
    docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml --profile full "$@"
  fi
}

for container in gaokao-mysql gaokao-redis gaokao-api gaokao-worker gaokao-frontend gaokao-nginx; do
  if docker inspect "$container" >/dev/null 2>&1; then
    echo "Refusing to reuse existing container: $container" >&2
    exit 2
  fi
done

started_at="$SECONDS"
cleanup() {
  status="$?"
  trap - EXIT
  if [ -f .env.full ]; then
    if [ "$status" -ne 0 ]; then
      compose ps >"$LOG_DIR/compose-ps.txt" 2>&1 || true
      compose logs --no-color --timestamps >"$LOG_DIR/compose-logs.txt" 2>&1 || true
      docker ps -a --format 'table {{.Names}}\t{{.Status}}' >"$LOG_DIR/docker-ps.txt" 2>&1 || true
      echo "Full Compose E2E FAILED; diagnostics saved for CI artifact" >&2
    fi
    compose down --volumes --remove-orphans --timeout 15 >/dev/null 2>&1 || true
    rm -f .env.full
  fi
  rm -rf "$TMP"
  echo "Full Compose E2E elapsed seconds: $((SECONDS - started_at))"
  exit "$status"
}
trap cleanup EXIT

# The helper creates random database, session and admin credentials (file mode 0600).
# Its password is captured in a private temporary file, never printed to CI logs.
python3 scripts/setup_full_env.py --out .env.full --role admin --username ci-admin >"$TMP/setup-output"
ADMIN_PASSWORD="$(sed -n 's/^admin password: //p' "$TMP/setup-output")"
if [ -z "$ADMIN_PASSWORD" ]; then
  echo "Failed to generate temporary admin credentials" >&2
  exit 1
fi
export CI_ADMIN_PASSWORD="$ADMIN_PASSWORD"
unset ADMIN_PASSWORD

# Render the actual Compose contract without starting a second deployment.
# Do not print resolved environment variables (they include credentials).
python3 - <<'PY'
import json
import os
import subprocess

flags = (
    "ENABLE_SYNTHETIC_AGENT_EXPERIMENT",
    "USER_MEMORY_ENABLED",
    "GAOBAO_CHAT_COMPACTION_ENABLED",
    "GAOBAO_CHAT_SNAPSHOT_ENABLED",
    "ENABLE_OUTBOUND_FETCH",
)
env = {key: val for key, val in os.environ.items() if key not in flags + ("APP_ENV", "SYNTHETIC_TASKRUN_JOURNAL")}
env["COMPOSE_DISABLE_ENV_FILE"] = "1"

def render(files, env_file, overrides=None, *, profile=None):
    cmd = ["docker", "compose", "--env-file", env_file]
    for file in files:
        cmd.extend(("-f", file))
    if profile is not None:
        cmd.extend(("--profile", profile))
    cmd.extend(("config", "--format", "json"))
    return json.loads(subprocess.check_output(cmd, env={**env, **(overrides or {})}))

local = render(["docker-compose.yml"], "/dev/null")
api = local["services"]["api"]["environment"]
assert api["APP_ENV"] == "development"
assert all(api[key] == "true" for key in flags[:3])
assert all(api[key] == "false" for key in flags[3:])
assert api["SYNTHETIC_TASKRUN_JOURNAL"] == "/app/data/synthetic-taskruns.sqlite"
for service in ("api", "frontend", "nginx"):
    assert all(port["host_ip"] == "127.0.0.1" for port in local["services"][service]["ports"])

disabled = render(["docker-compose.yml"], "/dev/null", {key: "false" for key in flags[:3]})
assert all(disabled["services"]["api"]["environment"][key] == "false" for key in flags[:3])

full_files = ["docker-compose.yml", "docker-compose.full.yml"]
full = render(full_files, ".env.full", profile="full")
assert {"mysql", "redis", "worker"} <= set(full["services"])
assert all(full["services"]["api"]["environment"][key] == "false" for key in flags)
opt_in = render(full_files, ".env.full", {"ENABLE_SYNTHETIC_AGENT_EXPERIMENT": "true"}, profile="full")
assert opt_in["services"]["api"]["environment"]["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] == "true"
print("Compose Agent profile contract PASS (local defaults, rollback, full isolation and explicit opt-in)")
PY

echo "Starting complete Compose profile (including all healthchecks)"
compose up --detach --build --wait --wait-timeout 360

for container in gaokao-mysql gaokao-redis gaokao-api gaokao-worker gaokao-frontend gaokao-nginx; do
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container")"
  echo "$container: $health"
  if [ "$health" != healthy ]; then
    echo "Expected healthy: $container" >&2
    exit 1
  fi
done

# Go through Nginx, not directly to an in-process TestClient or the API port.
python3 - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("FULL_COMPOSE_BASE_URL", "http://127.0.0.1").rstrip("/")
PASSWORD = os.environ["CI_ADMIN_PASSWORD"]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(method, path, body=None, token=None, headers=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    merged = {"Accept": "application/json"}
    if data is not None:
        merged["Content-Type"] = "application/json"
    if token:
        merged["Authorization"] = f"Bearer {token}"
    if headers:
        merged.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=merged, method=method)
    with OPENER.open(req, timeout=15) as response:
        payload = response.read()
        return response.status, json.loads(payload) if payload and "json" in response.headers.get("Content-Type", "") else payload

status, health = request("GET", "/healthz")
assert status == 200, status
status, html = request("GET", "/admin")
assert status == 200 and b"<html" in html.lower(), "Nginx frontend SPA route is unavailable"
status, ready = request("GET", "/api/v1/health/ready")
assert status == 200, ready

status, login = request("POST", "/api/v1/admin/auth/login", {"username": "ci-admin", "password": PASSWORD})
assert status == 200, login
token = login["access_token"]

status, schools = request("GET", "/api/v1/admin/schools?page_size=3", token=token)
assert status == 200 and schools["total"] >= 1, schools

key = "full-ci-" + uuid.uuid4().hex
status, created = request(
    "POST", "/api/v1/admin/quality/jobs", {"filters": {}}, token=token,
    headers={"Idempotency-Key": key},
)
assert status == 202, (status, created)
job_id = created["job"]["id"]
assert created["job"]["execution_mode"] == "async", created

deadline = time.monotonic() + 120
while True:
    status, detail = request("GET", f"/api/v1/admin/quality/jobs/{job_id}", token=token)
    assert status == 200, detail
    job = detail["job"]
    if job["status"] == "SUCCEEDED":
        break
    if job["status"] == "FAILED":
        raise AssertionError(f"Async job failed: {job['error_code']}")
    if time.monotonic() >= deadline:
        raise TimeoutError(f"Job {job_id} did not complete before deadline: {job['status']}")
    time.sleep(2)
assert job["execution_mode"] == "async", job

status, reviewed = request(
    "POST", f"/api/v1/admin/quality/jobs/{job_id}/review",
    {"decision": "APPROVED", "note": "full compose CI", "expected_version": job["version"]},
    token=token,
)
assert status == 200 and reviewed["review_status"] == "APPROVED", (status, reviewed)

status, audit = request("GET", "/api/v1/admin/audit-logs?page_size=100", token=token)
assert status == 200, audit
actions = {
    item["action"] for item in audit["items"]
    if item.get("resource_id") == str(job_id)
}
assert {"quality_job.succeeded", "quality_job.reviewed"} <= actions, actions

status, _ = request("POST", "/api/v1/admin/auth/logout", token=token)
assert status == 204, status
try:
    request("GET", "/api/v1/admin/auth/me", token=token)
except urllib.error.HTTPError as exc:
    assert exc.code == 401, exc.code
else:
    raise AssertionError("revoked token was still accepted")

print(json.dumps({
    "result": "PASS", "profile": "full-compose", "transport": "nginx",
    "job_id": job_id, "execution_mode": job["execution_mode"],
    "job_status": job["status"], "review": reviewed["review_status"],
    "audit_events": len(actions), "data": "SYNTHETIC_DEMO",
}, ensure_ascii=False))
PY

echo "Container resources (one-shot)"
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' \
  gaokao-mysql gaokao-redis gaokao-api gaokao-worker gaokao-frontend gaokao-nginx || true
echo "Full Compose E2E PASS"
