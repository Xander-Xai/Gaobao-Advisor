# Distributed Rate-Limit Reliability Governance Design

**Date:** 2026-11-27

**Repository baseline:** local cached `origin/master` at `8b636dbc76c35f85714cb3f6d74294eb9f9a2737` (PR #214 merge). Live remote refresh timed out twice during preparation; re-check before implementation/PR.

**Status:** design approved by the requester; pending document review before implementation planning.

## Goals and non-goals

Harden the existing Redis + Lua rate limiters used by the Java Gateway, FastAPI public HTTP middleware, and administrator endpoints. Preserve their current ownership boundaries and fail-closed behavior. Fix backward-clock timestamp regression, return useful rejection metadata, close the Industrial WebSocket exposure, and establish reproducible real-Redis and service-level evidence.

Do not replace the token buckets, create a rate-limit microservice, add Go, or create duplicate LLM RPM/TPM/model-concurrency controls. LLM usage quotas continue to be owned by GA01. Do not claim Redis TIME is monotonic or that asynchronous Redis failover provides strong consistency.

## Existing responsibility boundaries

| Surface | Current owner and keying | Intended boundary |
| --- | --- | --- |
| Public Java Catalog `/api/v1/catalog/**` | One Redis token bucket shared by all Gateway replicas; global capacity protection | Keep as a service-wide Catalog budget. It is not an IP/user quota. |
| Other Industrial FastAPI HTTP routes | Redis token bucket keyed by HMAC of the trusted-proxy-resolved client IP | Keep as public HTTP/IP protection across API replicas. Java Catalog routing does not fall through to this route. |
| Administrator login and job creation | Separate Redis sorted-set sliding windows; login by IP and username, jobs by IP and authenticated username | Keep endpoint-specific scopes and Redis atomic admission. |
| SSE chat | One public HTTP admission per request; request token is consumed before the stream begins | No connection lease is added in this change. Active-stream capacity is an explicit production acceptance item. |
| WebSocket voice `/ws/call` | Compatibility path; application rejects it in Industrial profile, while an Nginx location currently forwards it | Remove Industrial ingress forwarding and retain the application rejection. No message-level limiter is added to a route that is not released. |
| LLM provider limits | GA01-owned | No duplicate RPM, TPM, or model-concurrency mechanism. |

The global Catalog bucket and Python IP bucket are intentionally different controls and do not duplicate the same request: the Gateway routes Catalog paths to Java and sends the remaining `/api/**` paths to Python.

## Clock and Redis-state semantics

Both token bucket Lua scripts will use the same rule:

```text
effective_now = max(redis_now, previous_updated)
elapsed = max(0, effective_now - previous_updated)
tokens = min(capacity, previous_tokens + elapsed * rate)
updated = effective_now
```

The persisted timestamp never moves backward. A Redis clock rollback therefore cannot later replay the same elapsed interval as refill. Tokens remain bounded by configured capacity; refill calculations may be capped to the time needed to refill the bucket without changing the configured burst. A forward clock discontinuity may still refill up to the configured capacity. While Redis time remains behind a previously stored timestamp, no additional refill occurs until it catches up. These are bounded token-bucket semantics, not a monotonic-clock guarantee.

Lua decisions remain atomic: one script reads Redis time and bucket state, refills, conditionally debits one token, updates state, and refreshes TTL. The administrator sorted-set sliding window has a different state model but will also avoid moving its logical event time backward: before pruning, read the newest sorted-set score and use `window_now = max(redis_now_ms, newest_score)`. Pruning and newly admitted event scores use that effective time, so rollback cannot make a newly recorded attempt appear older than earlier attempts. If the key expires or is lost, its window state resets; expiry and Redis failover remain bounded by Redis clock, persistence, and replication behavior.

Redis replication or restart may lose a recent token-bucket state write depending on the deployment's persistence/replication guarantees. Lost token-bucket state initializes as a full bucket, allowing up to the configured burst again. Asynchronous failover cannot guarantee a globally strict quota; the supported durability/failover model must be stated by each deployment.

Backward-time integration tests will run a test-only variant derived from the production Lua source, replacing only the `TIME` read with a deterministic test argument. The production Lua source and production caller signature will have no clock override. Tests run against a disposable Redis instance and isolated key namespace; they must never alter host or shared Redis system time.

## Request behavior and configuration

- Token exhaustion returns HTTP 429 with integer `Retry-After` derived from the time needed to earn the next token; rounding is upward and the minimum is one second.
- Redis error, timeout, malformed/empty decision, or invalid stored state returns HTTP 503 without local fallback.
- Java and Python token bucket validation will use compatible positive, bounded rate/capacity ranges, and all replicas must use identical Redis, namespace/keying secret where applicable, rate, and capacity configuration.
- IP resolution trusts forwarding headers only if the direct peer belongs to an explicitly configured trusted proxy network. Otherwise the direct peer is the identity. Invalid forwarding chains do not grant a caller a spoofed identity.
- Admin sliding-window scopes remain separate from public token-bucket policy; their current Retry-After and 503 contracts are preserved, while their Redis-time pruning is guarded against backward timestamps.
- No new authenticated-user or high-cost per-route buckets are introduced without a concrete identity/quotas contract. The current IP layer is baseline abuse control; GA01 remains responsible for model quotas.

## Exposure and long-lived connections

The Industrial Nginx route for `/ws/` will be disabled (for example, return 404/404-equivalent without proxying); FastAPI's Industrial rejection remains defense in depth. Compatibility deployments can continue to expose the route only when explicitly configured outside the Industrial profile. If WebSocket is later formally released, it requires separate connection and message resource budgets before exposure.

SSE is covered by the normal HTTP request admission but not by an active-connection concurrency counter. This change will document that gap and require concurrency limits/measurements at the actual trusted ingress before production capacity acceptance. It will not add a process-local semaphore that would diverge across replicas or trust arbitrary forwarded IPs.

## Verification design

### Python and Java tests

- Unit tests verify backward timestamps do not regress, recovery after the test clock returns does not mint duplicate tokens, capacity is never exceeded, same-time calls debit atomically, and `Retry-After`/503 behavior is correct.
- Real Redis 7 tests use a random test-owned namespace and clean only those keys. Cover shared decisions across limiter instances and processes, concurrent debits, refill, capacity, independent client keys, backward/forward controlled time for both token buckets and admin windows, state deletion, connection timeout/disconnect, recovery, and invalid decisions.
- HTTP tests exercise forged `X-Forwarded-For` from untrusted peers, trusted-proxy chain resolution, 429 headers, and fail-closed 503. Gateway tests exercise global-key behavior independent of forwarded caller headers and 429 metadata.
- No `FLUSHDB`/`FLUSHALL`, shared Redis time manipulation, or production credentials are used.

### Redis and service fault tests

Use a disposable Compose project name, dedicated Redis 7 container, unique ports and test-owned volumes. Verify restart with persistence enabled and deliberate test-key loss separately. A single Redis container does not establish Sentinel/Cluster failover behavior. If no isolated replica topology is available, report failover as untested and state the persistence mode used.

### Controlled load measurements

Run against actual Python API and Java Gateway/Catalog service instances backed by the disposable real Redis. Record hardware/OS, container and resource limits, topology/replica counts, Redis persistence mode, limiter rates/capacities, workload path, duration, request count, concurrency, successful/429/503 counts, throughput, limiter-added latency if separable, and end-to-end P50/P95/P99. Record Redis CPU and memory before/during/after and recovery behavior. Do not infer production capacity, SLA, or HA from a local run. If the services cannot be launched or metrics cannot be collected reliably, mark the corresponding measurement incomplete instead of substituting Mock results.

## Documentation and delivery

Update the existing Industrial public HTTP admission and Java/Python performance/architecture documentation with the final semantics, test matrix, environment, results, and limitations. Keep test evidence separate from production readiness. The implementation branch should include only scoped limiter, routing/configuration, tests, and relevant documentation changes. Create/update an Issue and PR only after the current remote default-branch head and exact-head CI status are verified. Do not claim a merge or production acceptance without evidence.

## Acceptance criteria

1. Java and Python token bucket scripts use identical non-regressing timestamp semantics and retain atomic shared state operations; administrator sliding-window event scores also never move backward while the key exists.
2. Redis time rollback followed by recovery does not produce duplicate token refill or prematurely prune a newly recorded admin attempt; token count never exceeds configured capacity.
3. Redis errors and malformed decisions remain fail-closed (503); exhaustion is 429 with a valid `Retry-After`.
4. Untrusted forwarding headers cannot change rate-limit identity; explicitly trusted proxy chains resolve from right to left.
5. Industrial `/ws/` is not proxied to FastAPI, which continues to reject Industrial WebSocket handshakes.
6. Real Redis concurrent tests demonstrate cross-instance sharing, atomic debit, refill, capacity, isolation, disconnect/recovery, and test-key loss behavior.
7. Any load result names its actual environment and has enough observed metrics to be reproducible; unavailable metrics/topologies are recorded as acceptance gaps.
8. Documentation explicitly states Redis TIME, persistence, replica failover, admin-window clock behavior, and active SSE concurrency limitations without claiming absolute monotonicity or asynchronous strong consistency.
