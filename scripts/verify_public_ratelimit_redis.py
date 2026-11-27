#!/usr/bin/env python3
"""Exercise the production FastAPI rate-limit middleware against an owned Redis 7 container.

The container binds only to localhost, uses no persistent volume, and is always
removed by this script. Stopping/restarting it intentionally models both a Redis
outage and loss of volatile bucket state; it does not model replicated failover.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid

import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.middleware.ratelimit import RateLimitMiddleware


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_redis(client: redis.Redis, *, available: bool, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            is_available = bool(client.ping())
            if is_available == available:
                return
        except Exception as exc:
            last_error = exc
            if not available:
                return
        time.sleep(0.25)
    if available:
        raise TimeoutError("owned Redis container did not become ready") from last_error
    raise TimeoutError("owned Redis container did not stop accepting connections")


def main() -> None:
    name = f"gaobao-public-ratelimit-{uuid.uuid4().hex[:12]}"
    port = _free_port()
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:6379",
            "redis:7-alpine",
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    client: redis.Redis | None = None
    try:
        url = f"redis://127.0.0.1:{port}/0"
        os.environ["GAOBAO_RUNTIME_PROFILE"] = "industrial"
        os.environ["REDIS_URL"] = url
        os.environ["SESSION_SECRET"] = f"rate-limit-test-{uuid.uuid4().hex}"
        os.environ.pop("ADMIN_TRUSTED_PROXIES", None)
        os.environ.pop("TRUSTED_PROXIES", None)

        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5)
        _wait_for_redis(client, available=True)

        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, rate=0.001, capacity=2)

        @app.get("/probe")
        def probe():
            return {"ok": True}

        with TestClient(app, client=("198.51.100.44", 12001)) as http:
            admitted = [
                http.get("/probe", headers={"X-Forwarded-For": f"203.0.113.{index}"}).status_code for index in range(2)
            ]
            limited = http.get("/probe", headers={"X-Forwarded-For": "203.0.113.99"})
            assert admitted == [200, 200], admitted
            assert limited.status_code == 429, limited.status_code
            assert limited.headers.get("Retry-After") == "1000", limited.headers
            before = {
                "version": client.info("server")["redis_version"],
                "cpu": client.info("cpu"),
                "memory": client.info("memory"),
            }

            subprocess.run(["docker", "stop", name], check=True, capture_output=True, text=True, timeout=20)
            _wait_for_redis(client, available=False)
            unavailable = http.get("/probe")
            assert unavailable.status_code == 503, unavailable.status_code

            subprocess.run(["docker", "start", name], check=True, capture_output=True, text=True, timeout=20)
            _wait_for_redis(client, available=True)
            recovered = [http.get("/probe").status_code for _ in range(3)]
            assert recovered == [200, 200, 429], recovered
            after = {"cpu": client.info("cpu"), "memory": client.info("memory")}

        print(
            json.dumps(
                {
                    "result": "PASS",
                    "redis_version": before["version"],
                    "admitted_before_outage": admitted,
                    "throttle_before_outage": limited.status_code,
                    "retry_after_seconds": limited.headers["Retry-After"],
                    "status_during_outage": unavailable.status_code,
                    "statuses_after_restart_and_state_loss": recovered,
                    "redis_before_outage": {"cpu": before["cpu"], "memory": before["memory"]},
                    "redis_after_recovery": after,
                    "scope": "FastAPI RateLimitMiddleware over TestClient and disposable standalone Redis; no application business route or HA claim",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        if client is not None:
            client.close()
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True, text=True, timeout=20)


if __name__ == "__main__":
    main()
