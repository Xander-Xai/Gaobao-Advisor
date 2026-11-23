"""Tests for auth integration in chat and profile endpoints."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


class _FakeGraph:
    def invoke(self, state):
        return {
            "session_id": state["session_id"],
            "reply": "请补充你的省份和分数。",
            "slots": {"scene": state["scene"]},
        }


class TestChatTokenIssuance:
    @pytest.fixture(autouse=True)
    def _stub_finalization_io(self, monkeypatch):
        # These authentication tests stub the Graph and do not initialize DB
        # tables; persistence is covered by separate chat-integrity tests.
        monkeypatch.setattr(
            "server.routes.chat.memory_node",
            lambda state: {
                "trace": [*state.get("trace", []), {"node": "memory_update", "event": "persisted"}]
            },
        )
        monkeypatch.setattr(
            "server.routes.chat.quality_judge_node",
            lambda state: {"quality_grade": "pass"},
        )

    def test_chat_returns_session_token_in_done_event(self, monkeypatch):
        """Chat SSE response should include a session_token in the done event."""
        from server.auth import create_session_token

        monkeypatch.setattr("server.routes.chat.get_advisor_graph", lambda: _FakeGraph())
        token = create_session_token("test-auth-001")

        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "test-auth-001",
                "message": "你好",
                "scene": "gaokao",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        events = []
        for line in response.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        done_events = [event for event in events if event.get("type") == "done"]

        assert len(done_events) == 1
        assert "session_token" in done_events[0]

    def test_anonymous_chat_cannot_mint_token_for_caller_selected_session(self, monkeypatch):
        """A known session ID must never be sufficient to get its bearer proof."""
        from server.auth import verify_session_token

        monkeypatch.setattr("server.routes.chat.get_advisor_graph", lambda: _FakeGraph())
        requested_id = "test-known-other-session"
        response = client.post(
            "/api/v1/chat",
            json={"session_id": requested_id, "message": "你好", "scene": "gaokao"},
        )
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]
        done = next(item for item in events if item.get("type") == "done")
        assert done["session_id"].startswith("session-")
        assert done["session_id"] != requested_id
        assert verify_session_token(done["session_id"], done["session_token"])
        assert not verify_session_token(requested_id, done["session_token"])

        # Even if the anonymous client knows another session ID, the token
        # is only valid for the newly generated session, not the requested ID.
        unauthorized = client.get(
            f"/api/v1/profile/{requested_id}",
            headers={"Authorization": f"Bearer {done['session_token']}"},
        )
        assert unauthorized.status_code == 401

    def test_chat_rejects_invalid_or_cross_session_bearer(self, monkeypatch):
        """Invalid credentials fail before graph invocation."""
        from server.auth import create_session_token

        def forbidden_graph():
            raise AssertionError("Graph must not run for unauthorized chat")

        monkeypatch.setattr("server.routes.chat.get_advisor_graph", forbidden_graph)
        request = {"session_id": "test-victim-01", "message": "你好"}
        for token in ("bad-token", create_session_token("test-somebody-else")):
            response = client.post(
                "/api/v1/chat",
                json=request,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 401

    def test_valid_session_can_continue_without_rotating_identity(self, monkeypatch):
        from server.auth import create_session_token

        monkeypatch.setattr("server.routes.chat.get_advisor_graph", lambda: _FakeGraph())
        session_id = "test-owned-session-01"
        token = create_session_token(session_id)
        response = client.post(
            "/api/v1/chat",
            json={"session_id": session_id, "message": "继续"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        done = next(
            json.loads(line[6:])
            for line in response.text.split("\n")
            if line.startswith("data: ") and '"type": "done"' in line
        )
        assert done["session_id"] == session_id
        assert done["session_token"] == token


class TestProfileAuth:
    def test_profile_without_token_returns_401(self):
        """Profile GET without token should return 401."""
        response = client.get("/api/v1/profile/test-auth-001")
        assert response.status_code == 401

    def test_profile_with_valid_token_returns_200(self):
        """Profile GET with valid token should return 200."""
        from server.auth import create_session_token

        token = create_session_token("test-auth-001")
        response = client.get(
            "/api/v1/profile/test-auth-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_profile_with_invalid_token_returns_401(self):
        """Profile GET with invalid token should return 401."""
        response = client.get(
            "/api/v1/profile/test-auth-001",
            headers={"Authorization": "Bearer invalid-token-xxx"},
        )
        assert response.status_code == 401
