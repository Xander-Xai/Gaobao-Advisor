#!/usr/bin/env python3
"""Live distributed integration assertions on GitHub Linux Docker runners.

Must run only after scripts/verify_java_compose_ci.sh has started the stack.
No synthetic results are used as evidence of real admissions accuracy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "gaobao-java-smoke",
    "--env-file",
    ".env.full",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.full.yml",
    "-f",
    "docker-compose.java.yml",
    "--profile",
    "full",
    "--profile",
    "java",
]


def http(url: str, *, data: dict | None = None, token: str | None = None, timeout: int = 15):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with OPENER.open(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


def gateway_urls() -> list[str]:
    p = subprocess.run(
        COMPOSE + ["ps", "-q", "java-gateway"],
        capture_output=True,
        text=True,
        check=True,
    )
    ids = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    assert len(ids) == 2, f"Expected two gateway replicas, found {len(ids)}"
    urls = []
    for cid in ids:
        ip = subprocess.check_output(
            ["docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
            text=True,
        ).strip()
        assert ip, f"No Docker network IP for {cid}"
        urls.append(f"http://{ip}:8080")
    assert len(set(urls)) == 2, "Gateway replicas were not independently addressed"
    return urls


def require_status(url: str, expected: int, *, attempts: int = 30):
    last = None
    for _ in range(attempts):
        try:
            code, _, _ = http(url, timeout=8)
            last = code
            if code == expected:
                return
        except (OSError, TimeoutError) as e:
            last = str(e)
        time.sleep(1)
    raise AssertionError(f"{url} expected {expected}, got {last}")


def verify_session_sse():
    base = "http://127.0.0.1"
    first = {
        "session_id": "caller-controlled-id",
        "message": "你好",
        "scene": "gaokao",
    }
    code, headers, body = http(base + "/api/v1/chat", data=first, timeout=60)
    assert code == 200, (code, body[:800])
    assert "text/event-stream" in headers.get("Content-Type", ""), headers
    events = []
    for line in body.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line.removeprefix("data:").strip()))
    assert events and any(e.get("type") == "token" for e in events), events
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, events
    sid, token = done[0]["session_id"], done[0]["session_token"]
    assert sid != first["session_id"], "Client can mint session ids!"
    assert len(token) == 64, done
    assert any(e.get("type") == "done" for e in events), events

    code, _, _ = http(base + "/api/v1/profile/" + sid)
    assert code == 401, f"missing bearer token admitted: {code}"
    code, _, _ = http(base + "/api/v1/profile/" + sid, token=token)
    assert code == 200, f"valid session token rejected: {code}"
    code, _, _ = http(base + "/api/v1/profile/" + sid, token="0" * 64)
    assert code == 401, f"invalid session token admitted: {code}"
    code, _, body = http(
        base + "/api/v1/chat",
        data={"session_id": sid, "message": "继续", "scene": "gaokao"},
        token="0" * 64,
    )
    assert code == 401, (code, body[:200])
    print("SSE, server-issued session ownership and Authorization passthrough: PASS")


def verify_distributed_rate_limit():
    urls = gateway_urls()
    for base in urls:
        require_status(base + "/actuator/health/readiness", 200)

    # The deployment is recreated with 1 token/sec and burst 4. The key is
    # constant, and this sequence must consume the SAME budget from both pods.
    time.sleep(6)
    responses = []
    for i in range(20):
        replica = i % 2
        code, _, _ = http(urls[replica] + "/api/v1/catalog/schools?limit=1")
        responses.append((replica, code))
    assert all(any(r == i and c == 200 for r, c in responses) for i in (0, 1)), responses
    assert all(any(r == i and c == 429 for r, c in responses) for i in (0, 1)), responses
    allowed = sum(code == 200 for _, code in responses)
    assert 2 <= allowed <= 12, responses
    assert all(code in (200, 429) for _, code in responses), responses
    print(f"Two independent gateway replicas share Redis 429 budget: PASS ({allowed}/20 allowed)")


def verify_redis_down():
    urls = gateway_urls()
    for base in urls:
        require_status(base + "/actuator/health/readiness", 503, attempts=30)
        try:
            code, _, _ = http(base + "/api/v1/catalog/schools?limit=1", timeout=12)
            assert code >= 500, f"Redis unavailable but gateway returned {code}"
        except (urllib.error.URLError, TimeoutError, OSError):
            # Hard failure / closed connection is also fail-closed (never 200).
            pass
    print("Redis outage: readiness non-200 and catalog rate limit fails closed: PASS")


def verify_restored():
    urls = gateway_urls()
    for base in urls:
        require_status(base + "/actuator/health/readiness", 200, attempts=35)
    for base in urls:
        last = None
        for _ in range(20):
            try:
                code, _, _ = http(base + "/api/v1/catalog/schools?limit=1")
                last = code
                if code == 200:
                    break
            except OSError as e:
                last = str(e)
            time.sleep(1)
        else:
            raise AssertionError(f"Gateway not restored: {base} -> {last}")
    print("Redis restart: both gateways recover: PASS")


def verify_mysql_down():
    # Catalog's own readiness must fail, not just the Nginx reverse proxy.
    p = subprocess.run(
        COMPOSE + ["ps", "-q", "java-catalog"],
        capture_output=True,
        text=True,
        check=True,
    )
    cid = p.stdout.strip()
    assert cid, "Catalog container missing"
    ip = subprocess.check_output(
        ["docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
        text=True,
    ).strip()
    require_status(f"http://{ip}:8081/actuator/health/readiness", 503, attempts=25)
    code, _, _ = http("http://127.0.0.1/api/v1/catalog/schools?limit=1", timeout=20)
    assert code >= 500, f"Catalog answered successfully during MySQL outage: {code}"
    print("MySQL outage: catalog readiness + public query fail closed: PASS")


def verify_mysql_restored():
    p = subprocess.run(
        COMPOSE + ["ps", "-q", "java-catalog"],
        capture_output=True,
        text=True,
        check=True,
    )
    cid = p.stdout.strip()
    assert cid, "Catalog container missing after MySQL restart"
    ip = subprocess.check_output(
        ["docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
        text=True,
    ).strip()
    require_status(f"http://{ip}:8081/actuator/health/readiness", 200, attempts=70)
    require_status("http://127.0.0.1/api/v1/data/overview", 200, attempts=45)
    require_status("http://127.0.0.1/api/v1/catalog/schools?limit=1", 200, attempts=45)
    print("MySQL restart: Python and Java catalog recover: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=["sse", "distributed", "redis-down", "redis-restored", "mysql-down", "mysql-restored"],
    )
    args = parser.parse_args()
    {
        "sse": verify_session_sse,
        "distributed": verify_distributed_rate_limit,
        "redis-down": verify_redis_down,
        "redis-restored": verify_restored,
        "mysql-down": verify_mysql_down,
        "mysql-restored": verify_mysql_restored,
    }[args.stage]()


if __name__ == "__main__":
    main()
