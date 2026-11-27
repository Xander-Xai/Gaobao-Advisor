#!/usr/bin/env bash
# Integration smoke for the standard Java/MySQL/Redis topology on CI runners.
set -euo pipefail

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required" >&2
  exit 2
fi

if [[ -e .env.full ]]; then
  echo "Refusing to overwrite an existing .env.full" >&2
  exit 2
fi

PROJECT_NAME="${GAOBAO_RATE_LIMIT_COMPOSE_PROJECT:-gaobao-ratelimit-${GITHUB_RUN_ID:-local}-$$}"
HTTP_PORT="${RATE_LIMIT_HTTP_PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}"
if [[ ! "$HTTP_PORT" =~ ^[0-9]+$ ]] || ((HTTP_PORT < 1024 || HTTP_PORT > 65535)); then
  echo "RATE_LIMIT_HTTP_PORT must be an available unprivileged TCP port" >&2
  exit 2
fi
if ! python3 -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1", int(sys.argv[1]))); s.close()' "$HTTP_PORT"; then
  echo "RATE_LIMIT_HTTP_PORT is already in use" >&2
  exit 2
fi
export GAOBAO_RATE_LIMIT_COMPOSE_PROJECT="$PROJECT_NAME"
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
export GAOBAO_HTTP_PORT="$HTTP_PORT"
export GAOBAO_RATE_LIMIT_BASE_URL="http://127.0.0.1:$HTTP_PORT"
BASE_URL="$GAOBAO_RATE_LIMIT_BASE_URL"
RATE_LIMIT_RESULTS_DIR="${RATE_LIMIT_RESULTS_DIR:-benchmarks/load/results/$PROJECT_NAME}"
TMP="$(mktemp -d)"

existing_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT_NAME")"
existing_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT_NAME")"
if [[ -n "$existing_containers" || -n "$existing_volumes" ]]; then
  echo "Refusing to reuse Docker resources for Compose project $PROJECT_NAME" >&2
  rm -rf "$TMP"
  exit 2
fi

# This generated secret file is private, test-only, and removed by the trap.
python3 scripts/setup_full_env.py --role admin --username ci-admin >/dev/null

# Test-only limits allow measuring backend capacity rather than rate limiter 429.
# The Compose overlay defaults remain 20/s and 40 burst for local demo.
export PUBLIC_API_RATE_PER_SECOND=3000
export PUBLIC_API_RATE_BURST=3000
export CATALOG_RATE_PER_SECOND=3000
export CATALOG_RATE_BURST=3000

compose=(docker compose --project-name "$PROJECT_NAME" --env-file .env.full
  -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml
  --profile full --profile java)

cleanup() {
  local rc=$?
  if ((rc != 0)); then
    "${compose[@]}" ps || true
    "${compose[@]}" logs --tail=120 catalog-db-init java-catalog java-gateway api mysql redis nginx || true
  fi
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f .env.full
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" config --format json >"$TMP/compose.json"
COMPOSE_CONFIG="$TMP/compose.json" python3 - <<'PY'
import json
import os
with open(os.environ["COMPOSE_CONFIG"], encoding="utf-8") as f:
    cfg = json.load(f)
for name in ("api", "frontend"):
    assert not cfg["services"][name].get("ports"), f"{name} bypasses gateway on host"
assert cfg["services"]["nginx"].get("ports"), "Nginx has no public port"
assert {"mysql", "redis", "worker", "java-gateway", "java-catalog", "catalog-db-init"} <= set(cfg["services"])
api_env = cfg["services"]["api"]["environment"]
assert api_env["GAOBAO_CHAT_COMPACTION_ENABLED"] == "true"
assert api_env["GAOBAO_CHAT_SNAPSHOT_ENABLED"] == "true"
assert api_env["DATABASE_URL"].startswith("mysql+pymysql://")
assert api_env["REDIS_URL"].startswith("redis://")
print("Java overlay host port isolation: PASS")
PY
if [[ "${CONFIG_ONLY:-0}" == "1" ]]; then
  echo "Java Compose configuration validation: PASS"
  exit 0
fi
"${compose[@]}" up --build --detach

# Public paths must pass through Nginx -> Java gateway.
ready=0
for i in $(seq 1 90); do
  if curl --silent --fail --max-time 4 "$BASE_URL/api/v1/health" >/dev/null &&
     curl --silent --fail --max-time 4 "$BASE_URL/api/v1/catalog/schools?limit=2" >"$TMP/gaobao-java-schools.json"; then
    ready=1
    break
  fi
  sleep 3
done
if ((ready != 1)); then
  echo "Gateway/Catalog readiness timed out" >&2
  exit 1
fi

SCHOOLS_JSON="$TMP/gaobao-java-schools.json" GAOBAO_RATE_LIMIT_BASE_URL="$BASE_URL" python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request

with open(os.environ["SCHOOLS_JSON"], encoding="utf-8") as fp:
    result = json.load(fp)
assert isinstance(result["items"], list) and result["items"], result
assert result["count"] == len(result["items"]), result
assert result["total"] >= result["count"], result
assert any(row.get("is_synthetic") is True for row in result["items"]), result

base = os.environ["GAOBAO_RATE_LIMIT_BASE_URL"]
with urllib.request.urlopen(base + "/api/v1/data/overview", timeout=10) as response:
    overview = json.load(response)
assert overview["data_profile"] == "SYNTHETIC_DEMO", overview

try:
    urllib.request.urlopen(base + "/api/v1/catalog/schools?limit=201", timeout=10)
except urllib.error.HTTPError as exc:
    assert exc.code == 400, exc.code
else:
    raise AssertionError("expected 400 for invalid limit")

try:
    urllib.request.urlopen(base + "/api/v1/catalog/schools/99999999", timeout=10)
except urllib.error.HTTPError as exc:
    assert exc.code == 404, exc.code
else:
    raise AssertionError("expected 404 for unknown school")
print("Java + MySQL/Redis + Python public API smoke: PASS")
PY

# SSE and HMAC/session ownership across the real Nginx -> Java -> Python hop.
python3 scripts/verify_java_distributed_ci.py sse
python3 scripts/verify_java_distributed_ci.py ingress
# Compare Java read model with existing Python response semantics on live MySQL.
python3 scripts/verify_java_distributed_ci.py parity
# SQL-level read-only proof: allowed table SELECTs and denied private table/write/DDL.
python3 scripts/verify_java_distributed_ci.py reader

# Real Locust load profiles; all artifacts use SYNTHETIC_DEMO and a no-key AI path.
# The one->two comparison is a topology experiment, not a guaranteed speedup.
if [[ "${RUN_LOAD_BENCHMARK:-1}" == "1" ]]; then
  # Amplify only the synthetic CI MySQL dataset; never import real records.
  "${compose[@]}" exec -T api python -m benchmarks.load.seed_scale --schools 500 --confirm-synthetic-only
  python3 scripts/verify_java_distributed_ci.py parity
  python3 benchmarks/load/run_suite.py --scenario catalog --tiers 50 --duration 8 --label single-gateway --host "$BASE_URL" --output "$RATE_LIMIT_RESULTS_DIR" --informational
  "${compose[@]}" up --no-deps --detach --scale java-gateway=2 java-gateway
  sleep 5
  python3 benchmarks/load/run_suite.py --scenario catalog --tiers 50,100,200 --duration 8 --label dual-gateway --host "$BASE_URL" --output "$RATE_LIMIT_RESULTS_DIR" --informational
  python3 benchmarks/load/run_suite.py --scenario python --tiers 50,100,200 --duration 8 --label dual-gateway --host "$BASE_URL" --output "$RATE_LIMIT_RESULTS_DIR" --informational
  # All load tiers are observational: no calibrated performance SLO exists.
  # Zero/all-failed/partially-failed samples remain visible in uploaded JSON/CSV.
  # Functional API, SSE, permission and failure-injection checks remain hard gates.
  python3 benchmarks/load/run_suite.py --scenario python-overview --tiers 50 --duration 8 --label dual-gateway --host "$BASE_URL" --output "$RATE_LIMIT_RESULTS_DIR" --informational
  python3 benchmarks/load/run_suite.py --scenario chat --tiers 5,10 --duration 8 --label dual-gateway --host "$BASE_URL" --output "$RATE_LIMIT_RESULTS_DIR" --informational
fi

# Scale the same live cluster to two gateways with a deliberately tiny shared
# Redis budget, making cross-replica 429 behavior observable.
export CATALOG_RATE_PER_SECOND=1
export CATALOG_RATE_BURST=4
"${compose[@]}" up --no-deps --detach --scale java-gateway=2 --force-recreate java-gateway
"${compose[@]}" restart nginx
python3 scripts/verify_java_distributed_ci.py distributed
# Verify Nginx actually fans out to 2 backends, fails over after node stop,
# and re-discovers its IP without Nginx restart (dynamic upstream DNS).
python3 scripts/verify_java_distributed_ci.py ha

# A Redis or MySQL failure must not produce a successful catalog response.
"${compose[@]}" stop redis
python3 scripts/verify_java_distributed_ci.py redis-down
"${compose[@]}" start redis
python3 scripts/verify_java_distributed_ci.py redis-restored

sleep 6
"${compose[@]}" stop mysql
python3 scripts/verify_java_distributed_ci.py mysql-down
"${compose[@]}" start mysql
python3 scripts/verify_java_distributed_ci.py mysql-restored
