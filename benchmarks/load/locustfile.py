"""Locust scenarios against synthetic data only; validate payloads as well as latency."""

from __future__ import annotations

import json
from uuid import uuid4

from locust import HttpUser, constant_pacing, task


class CatalogUser(HttpUser):
    wait_time = constant_pacing(1.0)

    @task
    def schools(self):
        with self.client.get(
            "/api/v1/catalog/schools?limit=10",
            name="java/catalog/schools",
            timeout=10,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                return
            try:
                data = response.json()
                assert data["items"] and data["count"] == len(data["items"])
            except (ValueError, KeyError, AssertionError) as error:
                response.failure(f"invalid catalog response: {error}")


class PythonCatalogUser(HttpUser):
    wait_time = constant_pacing(1.0)

    @task
    def schools(self):
        # Equivalent bounded, paginated read workload to the Java Catalog API.
        with self.client.get(
            "/api/v1/data/schools?limit=10",
            name="python/data/schools",
            timeout=10,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                return
            try:
                data = response.json()
                assert data["items"] and data["count"] == len(data["items"])
            except (ValueError, KeyError, AssertionError) as error:
                response.failure(f"invalid Python response: {error}")


class PythonOverviewUser(HttpUser):
    """Expensive aggregate endpoint measured separately at a modest 50 users."""

    wait_time = constant_pacing(1.0)

    @task
    def overview(self):
        with self.client.get(
            "/api/v1/data/overview",
            name="python/data/overview",
            timeout=10,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                return
            try:
                data = response.json()
                assert data["data_profile"] == "SYNTHETIC_DEMO"
                assert data["counts"]["schools"] >= 500
            except (ValueError, KeyError, AssertionError) as error:
                response.failure(f"invalid overview response: {error}")


class NoKeyChatUser(HttpUser):
    wait_time = constant_pacing(2.0)

    @task
    def chat(self):
        with self.client.post(
            "/api/v1/chat",
            name="python/ai-chat/no-key-sse",
            # ChatRequest requires a session_id; an anonymous request gets a new
            # server-owned ID regardless of this untrusted client value.
            json={"session_id": f"load-{uuid4().hex}", "message": "你好", "scene": "gaokao"},
            timeout=40,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                return
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                response.failure("expected event-stream")
                return
            try:
                events = [
                    json.loads(line.removeprefix("data:").strip())
                    for line in response.text.splitlines()
                    if line.startswith("data:")
                ]
                assert any(event.get("type") == "done" for event in events)
            except (ValueError, AssertionError) as error:
                response.failure(f"missing SSE done event: {error}")
