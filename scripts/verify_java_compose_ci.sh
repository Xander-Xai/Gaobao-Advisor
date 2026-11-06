#!/usr/bin/env bash
# Integration smoke for the opt-in Java/MySQL/Redis topology on CI runners.
set -euo pipefail

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required" >&2
  exit 2
fi

python scripts/setup_full_env.py --force >/dev/null
export CATALOG_RATE_PER_SECOND=20
export CATALOG_RATE_BURST=40

compose=(docker compose --project-name gaobao-java-smoke --env-file .env.full
  -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml
  --profile full --profile java)

cleanup() {
  local rc=$?
  if ((rc != 0)); then
    "${compose[@]}" ps || true
    "${compose[@]}" logs --tail=120 java-catalog java-gateway api mysql redis nginx || true
  fi
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --build --detach

# Public paths must pass through Nginx -> Java gateway.
ready=0
for i in $(seq 1 90); do
  if curl --silent --fail --max-time 4 http://127.0.0.1/api/v1/health >/dev/null &&
     curl --silent --fail --max-time 4 'http://127.0.0.1/api/v1/catalog/schools?limit=2' >/tmp/gaobao-java-schools.json; then
    ready=1
    break
  fi
  sleep 3
done
if ((ready != 1)); then
  echo "Gateway/Catalog readiness timed out" >&2
  exit 1
fi

python3 - <<'PY'
import json
import urllib.error
import urllib.request

with open("/tmp/gaobao-java-schools.json", encoding="utf-8") as fp:
    result = json.load(fp)
assert isinstance(result["items"], list) and result["items"], result
assert result["count"] == len(result["items"]), result
assert result["total"] >= result["count"], result
assert any(row.get("is_synthetic") is True for row in result["items"]), result

base = "http://127.0.0.1"
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
