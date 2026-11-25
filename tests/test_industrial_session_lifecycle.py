"""Industrial session lifecycle regression; SQLite checks logical version fences."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import Conversation, ConversationMessage
from server.graph.nodes.memory import memory_node
from server.report.storage import ReportStorage
from server.services.industrial_session import SessionConflict, load_turn
from server.user_data_erasure import erase_live_session_data


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'industrial-session.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setattr("db.database.get_session", sessions)
    monkeypatch.setattr("server.graph.nodes.memory.get_session", sessions)
    yield sessions
    engine.dispose()


def persist(version, sid="industrial-session", score=600):
    result = memory_node(
        {
            "session_id": sid,
            "input_text": f"{score}分",
            "reply": "fixture reply",
            "slots": {"province": "广东", "score_rank": f"{score}分"},
            "atomic_memory": True,
            "industrial_turn_version": version,
        }
    )
    return result["trace"][-1]["event"]


def test_stale_turn_cannot_overwrite_newer_turn(factory):
    sid = "industrial-session"
    _, _, fresh = load_turn(sid, None, fresh=True)
    assert persist(fresh) == "persisted"
    _, _, first = load_turn(sid, None, fresh=False)
    _, _, second = load_turn(sid, None, fresh=False)
    assert persist(first, score=610) == "persisted"
    assert persist(second, score=620) == "persist_failed"
    with factory() as db:
        assert db.query(Conversation).one().score_rank == "610分"
        assert db.query(ConversationMessage).count() == 4


def test_erasure_rejects_old_inflight_turn_and_old_bearer_session(factory, tmp_path):
    sid = "industrial-session"
    _, _, fresh = load_turn(sid, None, fresh=True)
    assert persist(fresh) == "persisted"
    _, _, pending = load_turn(sid, None, fresh=False)
    assert erase_live_session_data(sid, ReportStorage(tmp_path / "reports"))["live_data_erased"]
    assert persist(pending) == "persist_failed"
    with pytest.raises(SessionConflict):
        load_turn(sid, None, fresh=False)
    with factory() as db:
        assert db.query(Conversation).count() == 0
        assert db.query(ConversationMessage).count() == 0


def test_fresh_version_only_creates_once(factory):
    sid = "industrial-session"
    _, _, version = load_turn(sid, None, fresh=True)
    assert persist(version) == "persisted"
    assert persist(version) == "persist_failed"
    with pytest.raises(SessionConflict):
        load_turn(sid, None, fresh=True)
    with factory() as db:
        assert db.query(Conversation).count() == 1
        assert db.query(ConversationMessage).count() == 2


def test_industrial_legacy_write_without_version_rejected(factory):
    assert persist(None) == "persist_failed"
    with factory() as db:
        assert db.query(Conversation).count() == 0


def test_deleted_bearer_cannot_reenter_chat_or_legacy_mutations(factory, monkeypatch):
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.auth import create_session_token
    from server.routes import chat, profile

    sid = "industrial-deleted"
    graph = Mock()
    monkeypatch.setattr(chat, "get_advisor_graph", lambda: graph)
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(profile.router)
    headers = {"Authorization": "Bearer " + create_session_token(sid)}
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"session_id": sid, "message": "600分"}, headers=headers)
        assert "SESSION_UNAVAILABLE" in response.text
        assert '"type": "done"' not in response.text
        update = client.put(f"/api/v1/profile/{sid}", json={"field": "score", "value": "600"}, headers=headers)
        assert update.status_code == 503
        assert client.get(f"/api/v1/profile/{sid}/next-question", headers=headers).status_code == 503
        skip = client.post(f"/api/v1/profile/{sid}/skip", json={"field": "interest"}, headers=headers)
        assert skip.status_code == 503
    graph.invoke.assert_not_called()
    with factory() as db:
        assert db.query(Conversation).count() == 0


def test_keyed_chat_reuses_committed_reply_and_deletes_receipt(factory, monkeypatch, tmp_path):
    import json
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from db.models import ChatTurnReceipt
    from server.auth import create_session_token
    from server.routes import chat

    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    sid = "industrial-session"
    _, _, fresh = load_turn(sid, None, fresh=True)
    assert persist(fresh) == "persisted"
    graph = Mock()
    graph.invoke.side_effect = lambda state: state
    monkeypatch.setattr(chat, "get_advisor_graph", lambda: graph)
    app = FastAPI()
    app.include_router(chat.router)
    headers = {
        "Authorization": "Bearer " + create_session_token(sid),
        "Idempotency-Key": "fixture-turn-00000001",
    }
    body = {"session_id": sid, "message": "600分", "slots": {"subject": "物理类"}}

    def events(response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    with TestClient(app) as client:
        first = events(client.post("/api/v1/chat", json=body, headers=headers))
        second = events(client.post("/api/v1/chat", json=body, headers=headers))
        assert first[-1]["type"] == second[-1]["type"] == "done"
        assert first[-1]["message_id"] == second[-1]["message_id"]
        assert first[:-1] == second[:-1]
        assert graph.invoke.call_count == 1
        changed = events(client.post("/api/v1/chat", json={**body, "message": "610分"}, headers=headers))
        assert changed[0]["code"] == "IDEMPOTENCY_CONFLICT"
        assert graph.invoke.call_count == 1
        with factory() as db:
            assert db.query(ConversationMessage).count() == 4
            assert db.query(ChatTurnReceipt).count() == 1
        erased = erase_live_session_data(sid, ReportStorage(tmp_path / "reports"))
        assert erased["deleted_row_counts"]["chat_turn_receipts"] == 1
        refused = events(client.post("/api/v1/chat", json=body, headers=headers))
        assert refused[0]["code"] == "SESSION_UNAVAILABLE"
        assert graph.invoke.call_count == 1
    with factory() as db:
        assert db.query(ChatTurnReceipt).count() == 0
        assert db.query(ConversationMessage).count() == 0


def test_keyed_chat_rejects_anonymous_and_invalid_keys(factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.auth import create_session_token
    from server.routes import chat

    app = FastAPI()
    app.include_router(chat.router)
    body = {"session_id": "industrial-session", "message": "600分"}
    with TestClient(app) as client:
        anonymous = client.post("/api/v1/chat", json=body, headers={"Idempotency-Key": "fixture-turn-00000001"})
        assert anonymous.status_code == 400
        invalid = client.post(
            "/api/v1/chat",
            json=body,
            headers={"Authorization": "Bearer " + create_session_token(body["session_id"]), "Idempotency-Key": "short"},
        )
        assert invalid.status_code == 400


def test_industrial_done_advertises_keyed_support_and_standard_refuses_key(factory, monkeypatch):
    import json
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routes import chat

    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    graph = Mock()
    graph.invoke.side_effect = lambda state: state
    monkeypatch.setattr(chat, "get_advisor_graph", lambda: graph)
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"session_id": "placeholder", "message": "600分"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        done = events[-1]
        assert done["type"] == "done"
        assert done["idempotency_supported"] is True
        monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "standard")
        refused = client.post(
            "/api/v1/chat",
            json={"session_id": done["session_id"], "message": "600分"},
            headers={
                "Authorization": "Bearer " + done["session_token"],
                "Idempotency-Key": "fixture-turn-00000001",
            },
        )
        assert refused.status_code == 409
        assert graph.invoke.call_count == 1
