# Industrial public HTTP admission

The FastAPI middleware keeps its process-local token bucket for lightweight and standard development environments. In the industrial profile it uses a Redis TIME/Lua token bucket keyed by an HMAC of the trusted-proxy-resolved client IP. All API replicas must use the same SESSION_SECRET and identical PUBLIC_API_RATE_PER_SECOND and PUBLIC_API_RATE_BURST settings against the same Redis. Rotation resets the affected rate buckets. A Redis error/invalid decision returns 503; exhaustion returns 429. There is no memory fallback. The two internal health probes are exempt and retain dependency-specific semantics.

The Java Catalog retains its separate Redis global bucket. This is public FastAPI HTTP admission (including the SSE request, not individual events); it does not rate-limit WebSocket messages or establish provider-cost/tenant quotas. Approved trusted proxy configuration, rate policy, network protection, load and failure testing are production gates.

Regression: tests/test_middleware_ratelimit.py covers middleware decisions, outage and local bucket expiry. tests/test_admin_limits_redis.py exercises the actual Redis script across two limiter instances and concurrent calls in the existing MySQL + Redis CI lane. These tests are not a production performance or resilience claim.
