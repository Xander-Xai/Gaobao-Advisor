# Distributed Rate-Limit Reliability Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden existing Java and Python Redis/Lua admission semantics and produce reproducible real-Redis, multi-instance, fault, and load evidence without changing the service ownership model.

**Architecture:** Keep the Java Catalog global token bucket, Python Industrial public HTTP per-IP bucket, and administrator Redis sliding windows. Align both token buckets on non-regressing Redis-time state and 429/Retry-After behavior; disable Industrial WebSocket proxying; use existing Compose/Locust infrastructure for isolated verification. Do not add a new limiter service, user quota system, SSE lease, or duplicate GA01 model quotas.

**Tech Stack:** Python 3 / FastAPI / redis-py / Lua / pytest, Java 21 / Spring Cloud Gateway / Reactive Redis / JUnit 5, Redis 7, Docker Compose, Nginx, Locust.

---

## File map

| File | Responsibility in this change |
| --- | --- |
| `server/middleware/ratelimit.py` | Python public token-bucket Lua, result parsing, 429 Retry-After integration. |
| `server/admin/limits.py` | Redis sliding-window clock handling using the newest active event score as non-regressing logical time. |
| `tests/test_middleware_ratelimit.py` | Python middleware status, trusted-proxy identity, and 429 header regressions. |
| `tests/test_admin_limits_redis.py` | Existing Redis-backed integration lane; add real Redis bucket atomicity/refill/capacity/isolation/clock/key-loss/fault cases. |
| `java-services/gateway/src/main/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterGatewayFilterFactory.java` | Java Catalog Lua parity, result contract, retry header, configuration validation, fail-closed behavior. |
| `java-services/gateway/src/test/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterTest.java` | Gateway 429/503/Retry-After/global-scope/configuration tests. |
| `deploy/java/nginx.conf` | Disable Industrial `/ws/` proxy route without changing non-Industrial compatibility routing. |
| `scripts/verify_java_compose_ci.sh` | Existing real Redis/MySQL/Gateway/Python Docker acceptance entrypoint; use unique Compose identity/ports for local execution and retain existing cleanup protections. |
| `scripts/verify_java_distributed_ci.py` | Live 2-Gateway shared bucket, trusted-edge, retry-header, Redis outage/recovery, and `/ws/` ingress assertions. |
| `benchmarks/load/locustfile.py` | Add focused scenarios that preserve successful/429 outcomes as separate measured request names. |
| `benchmarks/load/run_suite.py` | Report admitted, rate-rejected, server-error counts alongside latency and Redis/container CPU and memory. |
| `docs/security/industrial-public-http-admission.md` | Document time rollback, trust boundary, scopes, SSE/WebSocket, failure and Redis durability semantics. |
| `docs/performance/java-python-load.md` | Record actual local benchmark conditions/results and evidence limitations. |
| `docs/architecture/industrial-release-contract.md` | Update the reliability acceptance gate and state-loss/failover limitations. |

## Task 1: Python public bucket returns a safe, useful decision

**Files:** `server/middleware/ratelimit.py`, `tests/test_middleware_ratelimit.py`

- [ ] **Step 1: Add a middleware regression for calculated Retry-After.** Use an Industrial-profile FastAPI app with a fake Redis client returning `[0, 2]`; issue requests from the same client IP with `capacity=1`; assert the first response is 200, the second is 429 with `Retry-After: 2`, and malformed arrays produce 503.
- [ ] **Step 2: Run the focused test and confirm it fails against the old boolean-only Lua result.**

  Run: `uv run pytest tests/test_middleware_ratelimit.py -q`

  Expected: the new Retry-After and malformed-decision assertions fail before implementation; existing tests remain collected.
- [ ] **Step 3: Change the Python bucket Lua decision to return `{allowed, retry_seconds}`.** When blocked, compute `ceil((1 - tokens) / rate)` with a minimum of one second. Persist `updated = max(now, previous_updated)` and compute refill from that effective time. Validate persisted token/timestamp values; treat corrupt nonempty state as a backend error. Keep a missing key as a full initial bucket, cap tokens at capacity, and retain the existing TTL calculation.

  Replace the script body with this logic, retaining its existing `KEYS[1]`, `ARGV[1]` and `ARGV[2]` contract:

  ```lua
  local key = KEYS[1]
  local rate = tonumber(ARGV[1])
  local capacity = tonumber(ARGV[2])
  local t = redis.call('TIME')
  local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
  local previous = redis.call('HMGET', key, 'tokens', 'updated')
  local tokens = tonumber(previous[1])
  local updated = tonumber(previous[2])
  if previous[1] == false and previous[2] == false then
    tokens = capacity
    updated = now
  elseif not tokens or not updated or tokens < 0 or tokens > capacity then
    return redis.error_reply('invalid token bucket state')
  end
  local effective_now = math.max(now, updated)
  local elapsed = math.min(math.max(0, effective_now - updated), capacity / rate)
  tokens = math.min(capacity, tokens + elapsed * rate)
  local allowed = 0
  local retry_seconds = 0
  if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
  else
    retry_seconds = math.max(1, math.ceil((1 - tokens) / rate))
  end
  redis.call('HSET', key, 'tokens', tokens, 'updated', effective_now)
  redis.call('PEXPIRE', key, math.max(1000, math.ceil(capacity / rate * 2000)))
  return {allowed, retry_seconds}
  ```

- [ ] **Step 4: Add `RedisTokenBucketLimiter.check(ip) -> tuple[bool, int]` and preserve `allow(ip) -> bool` as a compatibility wrapper.** Parse exactly two integer results with allowed in `{0,1}` and retry nonnegative; on Redis exceptions or malformed values raise `SecurityBackendUnavailable`. Update middleware to use `check` and set `Retry-After` from its result.

  The wrapper behavior is:

  ```python
  def check(self, ip: str) -> tuple[bool, int]:
      try:
          result = self.client.eval(
              _REDIS_TOKEN_BUCKET, 1, self.key_for(ip), str(self.rate), str(self.capacity)
          )
      except Exception as exc:
          raise SecurityBackendUnavailable("Public rate-limit backend unavailable") from exc
      if not isinstance(result, (list, tuple)) or len(result) != 2:
          raise SecurityBackendUnavailable("Invalid public rate-limit decision")
      try:
          allowed, retry_after = int(result[0]), int(result[1])
      except (TypeError, ValueError) as exc:
          raise SecurityBackendUnavailable("Invalid public rate-limit decision") from exc
      if allowed not in (0, 1) or retry_after < 0 or (allowed == 1 and retry_after != 0):
          raise SecurityBackendUnavailable("Invalid public rate-limit decision")
      return allowed == 1, retry_after

  def allow(self, ip: str) -> bool:
      return self.check(ip)[0]
  ```

  In middleware, use the integer returned by `check` for the existing 429 `Retry-After` response; never catch `SecurityBackendUnavailable` as an allow decision.
- [ ] **Step 5: Run focused Python tests.**

  Run: `uv run pytest tests/test_middleware_ratelimit.py -q`

  Expected: all middleware tests pass, including invalid response => 503 and local limiter's existing 429 behavior.
- [ ] **Step 6: Commit the Python bucket behavior and tests.**

  Run: `git add server/middleware/ratelimit.py tests/test_middleware_ratelimit.py && git commit -m "fix(ratelimit): preserve Python bucket time and retry metadata"`

## Task 2: Java Catalog bucket has matching time and response semantics

**Files:** `java-services/gateway/src/main/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterGatewayFilterFactory.java`, `java-services/gateway/src/test/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterTest.java`

- [ ] **Step 1: Add JUnit regressions for decision arrays and HTTP headers.** Mock Redis to return allowed `[1,0]` and blocked `[0,2]`; assert blocked requests do not call downstream, use 429, and contain `Retry-After: 2`. Assert empty, malformed, timeout, and Redis error still return 503.
- [ ] **Step 2: Run the Gateway test before implementation.**

  Run: `mvn -B -ntp -f java-services/pom.xml -pl gateway -am test`

  Expected: new result-array/header tests fail because current script emits only a scalar.
- [ ] **Step 3: Make Java Lua mirror the Python rule.** Read `redis.call('TIME')`, calculate `effective_now = math.max(now, updated)`, refill from nonnegative elapsed time, cap at capacity, and persist `effective_now`. Return a two-element Redis array: decision and integer retry seconds. Preserve the single atomic Redis script and TTL.

  Use the same Lua transition as Task 1 Step 3. Java's source must not fork the state math: the lines for `effective_now`, `elapsed`, token refill/cap, debit, and `Retry-After` calculation must be byte-for-byte equivalent.
- [ ] **Step 4: Parse the two-element Redis result in the Gateway filter.** Accept only a well-formed decision and nonnegative retry value; map allow to the chain, throttle to 429 plus `Retry-After`, and all execution/timeout/invalid-result paths to 503.

  On a blocked decision set the response as follows before `setComplete()`:

  ```java
  exchange.getResponse().setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
  exchange.getResponse().getHeaders().set("Retry-After", Long.toString(retryAfter));
  return exchange.getResponse().setComplete();
  ```
- [ ] **Step 5: Align validation bounds with Python (`0 < rate <= 10000`, `0 < capacity <= 10000`) and add boundary tests.** Keep Gateway config integer-valued unless repository configuration requires otherwise; reject above-limit startup configuration.
- [ ] **Step 6: Run all Gateway tests.**

  Run: `mvn -B -ntp -f java-services/pom.xml -pl gateway -am test`

  Expected: all Java module tests pass.
- [ ] **Step 7: Commit Java Lua parity and response metadata.**

  Run: `git add java-services/gateway/src/main/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterGatewayFilterFactory.java java-services/gateway/src/test/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterTest.java && git commit -m "fix(gateway): align catalog limiter clock semantics"`

## Task 3: Real Redis tests cover shared bucket math and failures

**Files:** `server/admin/limits.py`, `tests/test_admin_limits_redis.py`, `tests/test_middleware_ratelimit.py`

- [ ] **Step 1: Add a test-only Lua clock helper in `tests/test_admin_limits_redis.py`.** Derive an integration script from the production Python Lua by replacing only the exact Redis `TIME` read with `tonumber(ARGV[3])`; keep production source/caller signature unchanged. Invoke the derived script against the real Redis test client and only keys under `TEST_REDIS_ROOT`.

  Use this exact replacement and assert it changed the script before executing it:

  ```python
  time_read = "local t = redis.call('TIME')\nlocal now = tonumber(t[1]) + tonumber(t[2]) / 1000000"
  controlled_script = _REDIS_TOKEN_BUCKET.replace(time_read, "local now = tonumber(ARGV[3])", 1)
  assert controlled_script != _REDIS_TOKEN_BUCKET
  ```

  Each test call passes arguments `rate`, `capacity`, then a decimal timestamp; production `RedisTokenBucketLimiter.check()` continues to pass only the first two arguments.
- [ ] **Step 2: Add real Redis tests for two-instance sharing, concurrent debit, capacity, and isolation.** Use separate `RedisTokenBucketLimiter` objects with the same namespace/secret and 20+ threads; assert admitted count equals capacity, stored token count is between zero and capacity, same-client buckets share, and different clients do not. Also start 10 independent Python processes, each making one request against the same Redis namespace and key; with capacity 5, assert exactly five processes are admitted.
- [ ] **Step 3: Add controlled-time rollback/recovery coverage for both token bucket production Lua sources.** Read the Java `LUA` text block from `java-services/gateway/src/main/java/com/xsuperplay/gaobao/gateway/FailClosedCatalogRateLimiterGatewayFilterFactory.java`, derive a test-only clock variant with the same single TIME-read replacement used for Python, and execute each script on real Redis. With rate 1 token/s and capacity 4, drain the bucket at time 1000, invoke at 1001, then move test time backward to 900 and forward to 1001. Assert each source keeps `updated=1001`, does not replay 101 seconds of refill, and never stores more than four tokens. Keep the scripts' normalized `effective_now`, `elapsed`, refill, and update expressions equal; if source extraction or math parity fails, fail the test.
- [ ] **Step 4: Add controlled-time rollback/recovery coverage for the admin sliding window.** Derive `_LUA_SLIDING_WINDOW` with `TIME` replaced by a test argument and execute it against Redis. With limit 2/window 60s, add two events at 100000ms, move time to 90000ms and confirm the next check is still rejected, then recover to 100001ms and confirm both old events remain until logical time reaches 160000ms. At 160000ms, confirm the old scores are pruned and a request is admitted. Assert a new event score never drops below the previous newest score.
- [ ] **Step 5: Guard admin sliding-window timestamps against rollback.** In `_LUA_SLIDING_WINDOW`, read the newest ZSET score before pruning and calculate `window_now = max(redis_now_ms, tonumber(newest[2]))` when a score exists. Use `window_now` for cutoff, Retry-After, new member score, and subsequent pruning. Keep the `(allowed, retry_after)` return contract.

  The Lua transition is:

  ```lua
  local t = redis.call('TIME')
  local redis_now_ms = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
  local newest = redis.call('ZREVRANGE', key, 0, 0, 'WITHSCORES')
  local window_now = redis_now_ms
  if newest[2] then
    window_now = math.max(window_now, tonumber(newest[2]))
  end
  local cutoff = window_now - window_ms
  redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
  ```

- [ ] **Step 6: Add controlled forward-jump and key-loss coverage.** A token bucket forward jump may refill only to capacity. Deleting a token test key simulates state loss; the next request gets only the documented fresh-bucket burst, and subsequent requests are limited normally.
- [ ] **Step 7: Add Redis disconnect/recovery and malformed result coverage using a disposable Redis container or existing `REDIS_TEST_URL`.** Stop only the test-owned container for the outage case; assert Python middleware returns 503 while unavailable and recovers after Redis starts. Do not run key deletion or container stop against an unowned `REDIS_TEST_URL` unless the operator explicitly configured a disposable instance.
- [ ] **Step 8: Run the Redis-marked tests with a dedicated Redis 7 instance.**

  Run: `REDIS_TEST_URL="${REDIS_TEST_URL:?set to the disposable Redis 7 endpoint}" uv run pytest tests/test_admin_limits_redis.py -q`

  Expected: all limiter integration tests pass; report the actual Redis version and number executed. A skipped suite is not a pass.
- [ ] **Step 9: Commit integration coverage.**

  Run: `git add tests/test_admin_limits_redis.py tests/test_middleware_ratelimit.py && git commit -m "test(ratelimit): verify Redis bucket rollback and recovery"`

## Task 4: Industrial ingress and service-level fail-closed checks

**Files:** `deploy/java/nginx.conf`, `scripts/verify_java_distributed_ci.py`, `scripts/verify_java_compose_ci.sh`

- [ ] **Step 1: Add live verification assertions before changing Nginx.** Extend `verify_java_distributed_ci.py` to assert each of two Gateway replicas consumes the same tiny Redis Catalog budget, blocked responses include valid `Retry-After`, caller-supplied `X-Forwarded-For` does not alter the Java global bucket, and `GET /ws/call` through Industrial Nginx returns a non-upstream 404.
- [ ] **Step 2: Add explicit Industrial Nginx `/ws/` rejection.** Replace the proxy location in `deploy/java/nginx.conf` with `location /ws/ { return 404; }`; do not alter the general development Nginx configuration. Keep FastAPI's existing Industrial WebSocket rejection.

  The Nginx location is exactly:

  ```nginx
  location /ws/ {
      return 404;
  }
  ```
- [ ] **Step 3: Make local Java Compose verification isolated.** Replace the fixed project name with exported `COMPOSE_PROJECT_NAME` defaulting to `gaobao-ratelimit-${GITHUB_RUN_ID:-local}-$$`; let `RATE_LIMIT_HTTP_PORT` override an automatically selected currently-free localhost port and pass it as `GAOBAO_HTTP_PORT` to Compose. Generate the admin/MySQL env file under `mktemp` (mode 0600), refuse to overwrite `.env.full` or any caller-provided env file, and remove only the temporary file and selected Compose project in the exit trap. Update `verify_java_distributed_ci.py` and `benchmarks/load/run_suite.py` to derive their Compose project from the same `COMPOSE_PROJECT_NAME`. Reuse the existing Compose overlay, which creates MySQL/Redis/Gateway/Catalog/Python services and tests two Gateway replicas, Redis down/restart, and Nginx re-discovery.
- [ ] **Step 4: Run script and Java filter checks before starting containers.**

  Run: `python scripts/verify_java_distributed_ci.py --help`

  Run: `mvn -B -ntp -f java-services/pom.xml -pl gateway -am test`

  Expected: successful stage argument parsing and passing Java tests. The compose verifier must render `docker compose config --quiet` using its own generated environment before any service starts.
- [ ] **Step 5: Run the full real service acceptance once the isolated stack is prepared.**

  Run: `RUN_LOAD_BENCHMARK=0 scripts/verify_java_compose_ci.sh`

  Expected: actual two-Gateway sharing, HTTP 429/Retry-After, Industrial WebSocket rejection, Catalog and Python HTTP 503 during Redis outage, and both paths' recovery pass through real Redis 7. Capture service logs on failure; cleanup removes only the script-owned stack and volumes.
- [ ] **Step 6: Commit ingress and live-service assertions.**

  Run: `git add deploy/java/nginx.conf scripts/verify_java_distributed_ci.py scripts/verify_java_compose_ci.sh && git commit -m "fix(ingress): close industrial voice websocket route"`

## Task 5: Measure limiter outcomes on actual service instances

**Files:** `benchmarks/load/locustfile.py`, `benchmarks/load/run_suite.py`, `scripts/verify_java_compose_ci.sh`

- [ ] **Step 1: Add focused Catalog and Python limiter Locust users.** Use the public Catalog path for the Java global bucket and `/api/v1/data/schools` for the Python per-IP bucket. For each response, classify 200 as admitted, 429 as rate-rejected only when `Retry-After` is an integer >= 1, and all other statuses as server/transport errors. Record admitted and rejected requests with separate Locust request names so aggregate CSVs retain the observed counts and latencies for each class.

  For both new users, implement the same response classification:

  ```python
  scope = "java/catalog/limiter"  # use "python/http/limiter" for the Python user
  if response.status_code == 200:
      response.request_meta["name"] = f"{scope}/admitted"
      response.success()
  elif response.status_code == 429 and response.headers.get("Retry-After", "").isdigit():
      response.request_meta["name"] = f"{scope}/rejected-429"
      response.success()
  else:
      response.request_meta["name"] = f"{scope}/unexpected"
      response.failure(f"unexpected limiter response: {response.status_code}")
  ```

  Use Locust's supported request-context/name override mechanism for the installed version; if `request_meta` is not a supported mutation point, use `catch_response=True` and report explicit admitted/rejected counters through Locust events while keeping raw status and latency artifacts.
- [ ] **Step 2: Extend `run_suite.py` with per-class result fields.** Parse the per-name Locust aggregate rows, emit request/admitted/rejected/server-error counts and latency percentiles for each, and retain the existing P50/P95/P99 and two-second Docker CPU/memory sampler. Include configured rate, capacity, Redis persistence, service/replica topology, duration, users, commit SHA, and host/OS in JSON output.
- [ ] **Step 3: Add CLI controls for scenario and actual configured rate/capacity without weakening production defaults.** Keep existing 3000/s CI throughput measurements separate. The limiter scenario runs with explicitly declared small test budgets and an isolated Redis namespace/Compose stack; never change normal deployment defaults to make a benchmark pass.
- [ ] **Step 4: Run controlled benchmark tiers against the running Nginx ingress.** Start with 10, 25, and 50 clients for 30 seconds per Java Catalog and Python route; record full artifacts and compare request totals against Redis script state/decision metrics. Capture Redis `INFO CPU`, `INFO MEMORY`, and Docker stats before, during, and after. Repeat only if invalid samples or measurement errors require correction.
- [ ] **Step 5: Document measured values or explicit measurement gaps.** Update `docs/performance/java-python-load.md` with hardware, OS, exact Compose project/topology, limits, data profile, routes, rates, test duration/users, admitted/429/503 totals, RPS, latency percentiles, Redis CPU/memory, and recovery. State that these local measurements are not production capacity/SLA/HA evidence.
- [ ] **Step 6: Commit benchmark and evidence docs.**

  Run: `git add benchmarks/load/locustfile.py benchmarks/load/run_suite.py docs/performance/java-python-load.md && git commit -m "test(perf): measure Redis rate limiter outcomes"`

## Task 6: Document operating semantics and acceptance limits

**Files:** `docs/security/industrial-public-http-admission.md`, `docs/architecture/industrial-release-contract.md`, `docs/performance/java-python-load.md`

- [ ] **Step 1: Update HTTP admission documentation.** State exact scope boundaries (Catalog global, Python per-IP, admin sliding windows, SSE request admission), trusted proxy contract, matching timestamp algorithm, bounded forward-time behavior, rollback pause, 429 Retry-After, and fail-closed 503.
- [ ] **Step 2: Update the Industrial release reliability gate.** State Redis 7 persistence/replication assumptions, full-bucket behavior after key/state loss, the lack of asynchronous strong consistency, untested managed failover when no replica lab exists, Industrial WebSocket closed at Nginx and app, and active SSE concurrency as a production acceptance gate.
- [ ] **Step 3: Add interview-ready implementation notes and a Mermaid sequence diagram.** Diagram request → Nginx → Java Gateway or Python API → Redis Lua TIME/read/refill/debit/write/TTL → 200/429, plus Redis timeout → 503. Explain the exact effective-time formula and its clock/failover limits in concise comments below the diagram.
- [ ] **Step 4: Run documentation checks and targeted tests.**

  Run: `git diff --check`

  Run: `uv run pytest tests/test_middleware_ratelimit.py -q`

  Run: `mvn -B -ntp -f java-services/pom.xml -pl gateway -am test`

  Expected: no whitespace errors; Python and Gateway tests pass.
- [ ] **Step 5: Commit documentation.**

  Run: `git add docs/security/industrial-public-http-admission.md docs/architecture/industrial-release-contract.md docs/performance/java-python-load.md && git commit -m "docs(ratelimit): record clock and failover limits"`

## Task 7: Final verification, review, and GitHub handoff

**Files:** all files listed above; no unrelated files.

- [ ] **Step 1: Run the full Python tests and lint/format checks required by the repository.**

  Run: `uv run pytest tests/test_middleware_ratelimit.py tests/test_admin_limits_redis.py -q` with `REDIS_TEST_URL` set to the isolated Redis 7 instance.

  Run: `uv run ruff check server/middleware/ratelimit.py tests/test_middleware_ratelimit.py tests/test_admin_limits_redis.py`

  Run: `uv run ruff format --check server/middleware/ratelimit.py tests/test_middleware_ratelimit.py tests/test_admin_limits_redis.py`

  Expected: no failures; report all skips and their reasons.
- [ ] **Step 2: Run Java tests and configuration checks.**

  Run: `mvn -B -ntp -f java-services/pom.xml -pl gateway -am test`

  Run: `RUN_LOAD_BENCHMARK=0 scripts/verify_java_compose_ci.sh`

  Expected: successful Java suite and valid Compose configuration.
- [ ] **Step 3: Run the actual-service Redis fault matrix and load profile.** Record exact outputs, Redis/Java/Python topology, persistence mode, Docker resource snapshots, and which requested failover cases were not testable.
- [ ] **Step 4: Review the full branch diff.** Run `git status --short`, `git diff --check`, `git diff origin/master...HEAD`, and `git log --oneline origin/master..HEAD`. Remove no user files; ensure only rate-limit code, deployment config, tests, benchmark, and docs are included.
- [ ] **Step 5: Re-check live remote `master`, current PR/Issue state, and exact-head CI.** If GitHub metadata remains unavailable, do not claim current status or create a PR against an unverified base. Create/update a focused Issue and PR only when the live base and checks are known; include test and performance evidence plus explicit untested production gates.

## Spec coverage review

- Clock rollback, recovery, forward discontinuity, bounded capacity, state loss, admin sliding-window behavior, and Redis failover limits: Tasks 1–3 and 6.
- Java/Python consistent semantics, atomic Lua behavior, Redis outage 503 and recovery: Tasks 1–4.
- Java global/Python IP/admin scope boundaries and trusted proxy forgery: Tasks 3–4 and 6.
- WebSocket and SSE boundaries: Tasks 4 and 6.
- Real Redis multi-instance/concurrency/refill/TTL/isolation/state-loss tests: Tasks 3–4.
- Actual Python/Java service performance, request classification, latency percentiles and Redis resource metrics: Task 5.
- Architecture docs, interview diagram, PR/Issue/Branch and exact-head checks: Tasks 6–7.

## Self-review notes

- The controlled clock substitution is test-only and leaves production Lua and caller signatures unchanged.
- Test code may delete or stop only resources it created under the unique test namespace/project; no shared DB flush or clock change is permitted.
- Existing production rate defaults remain unchanged; test limits are isolated and explicitly reported.
- Managed primary/replica failover, production TLS/WAF, real-user fairness, active SSE concurrency, and production capacity remain acceptance gates unless real infrastructure and evidence become available.
