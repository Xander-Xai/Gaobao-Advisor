"""Cross-task user-memory contract: consent, isolation, TTL, conflict and SSE integration."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import UserMemoryEntry, UserMemoryVault, _utcnow
from server import user_memory as service
from server.routes import chat as chat_routes
from server.routes import user_memory as memory_routes


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "user-memory.sqlite"),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "get_session", factory)
    monkeypatch.setattr("db.database.get_session", factory)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("USER_MEMORY_ENABLED", "true")
    app = FastAPI()
    app.include_router(memory_routes.router)
    app.include_router(chat_routes.router)
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def create_token(client):
    response = client.post("/api/v1/memory/vault")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["memory_token"]


def headers(token):
    return {"X-User-Memory-Token": token}


def put(client, token, key="preferred_regions", value="华南", version=0, ttl=180):
    return client.put(
        f"/api/v1/memory/entries/{key}",
        json={"value": value, "expected_version": version, "ttl_days": ttl},
        headers=headers(token),
    )


def test_feature_default_off_rejects_create_and_read(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_MEMORY_ENABLED", raising=False)
    app = FastAPI()
    app.include_router(memory_routes.router)
    with TestClient(app) as client:
        assert client.post("/api/v1/memory/vault").status_code == 503
        assert client.get("/api/v1/memory/entries", headers=headers("mem1_" + "a" * 64)).status_code == 503


def test_explicit_write_persists_across_requests_without_plaintext_token(client_db):
    client, factory = client_db
    token = create_token(client)
    saved = put(client, token)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["source"] == "EXPLICIT_USER"
    retrieved = client.get("/api/v1/memory/entries", headers=headers(token))
    assert retrieved.status_code == 200
    assert retrieved.json()["items"][0]["value"] == "华南"
    assert retrieved.json()["automatic_capture"] is False
    assert token not in str(retrieved.json())
    with factory() as db:
        assert db.query(UserMemoryVault).count() == 1
        assert db.query(UserMemoryVault).first().token_hash != token
        assert len(db.query(UserMemoryVault).first().token_hash) == 64


def test_owner_isolation_and_no_session_credential_reuse(client_db):
    client, _ = client_db
    first, second = create_token(client), create_token(client)
    assert put(client, first).status_code == 200
    assert client.get("/api/v1/memory/entries", headers=headers(second)).json()["items"] == []
    assert put(client, second, value="华北").status_code == 200
    assert client.get("/api/v1/memory/entries", headers=headers(first)).json()["items"][0]["value"] == "华南"
    assert client.get("/api/v1/memory/entries").status_code == 401
    assert client.get("/api/v1/memory/entries", headers=headers("session-token")).status_code == 401
    assert client.get("/api/v1/memory/entries", headers=headers("mem1_" + "A" * 64)).status_code == 401


def test_compare_and_swap_blocks_stale_update_and_expired_resurrection(client_db):
    client, factory = client_db
    token = create_token(client)
    assert put(client, token, version=1).status_code == 409
    assert put(client, token).json()["version"] == 1
    updated = put(client, token, value="西南", version=1)
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert put(client, token, value="过时覆盖", version=1).status_code == 409
    with factory() as db:
        row = db.query(UserMemoryEntry).one()
        row.expires_at = _utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/memory/entries", headers=headers(token)).json()["items"] == []
    assert put(client, token, value="过期覆盖", version=2).status_code == 409
    recreated = put(client, token, value="重新确认", version=0)
    assert recreated.json()["version"] == 3
    assert put(client, token, version=1).status_code == 409
    assert put(client, token, version=2).status_code == 409


def test_validation_ttl_and_enum_are_restricted(client_db):
    client, _ = client_db
    token = create_token(client)
    assert put(client, token, key="score", value="610").status_code == 422
    assert put(client, token, key="family", value="personal details").status_code == 422
    assert put(client, token, key="response_style", value="run arbitrary commands").status_code == 422
    assert put(client, token, key="response_style", value="concise").status_code == 200
    assert put(client, token, key="preferred_regions", value="a\nb").status_code == 422
    assert put(client, token, key="preferred_regions", value="x" * 121).status_code == 422
    assert put(client, token, key="preferred_regions", value="华北", ttl=366).status_code == 422


def test_delete_rotate_and_vault_erasure_revoke_credentials(client_db):
    client, factory = client_db
    token = create_token(client)
    assert put(client, token).status_code == 200
    deleted = client.delete("/api/v1/memory/entries/preferred_regions", headers=headers(token))
    assert deleted.json()["deleted"] is True
    assert client.delete("/api/v1/memory/entries/preferred_regions", headers=headers(token)).status_code == 404
    assert put(client, token).status_code == 200
    rotate = client.post("/api/v1/memory/vault/rotate", headers=headers(token))
    assert rotate.status_code == 200
    new_token = rotate.json()["memory_token"]
    assert token != new_token
    assert client.get("/api/v1/memory/entries", headers=headers(token)).status_code == 401
    assert client.get("/api/v1/memory/entries", headers=headers(new_token)).json()["count"] == 1
    erased = client.delete("/api/v1/memory/vault", headers=headers(new_token))
    assert erased.json()["deleted_entries"] == 1
    assert erased.json()["credential_revoked"] is True
    assert erased.json()["backups_and_external_processors_erased"] is False
    assert client.get("/api/v1/memory/entries", headers=headers(new_token)).status_code == 401
    with factory() as db:
        assert db.query(UserMemoryVault).count() == 0
        assert db.query(UserMemoryEntry).count() == 0


def test_cross_session_chat_injects_only_explicit_owner_memory(client_db, monkeypatch):
    client, _ = client_db
    token = create_token(client)
    assert put(client, token, key="response_style", value="concise").status_code == 200
    seen = []

    class StubGraph:
        def invoke(self, initial):
            seen.append(initial)
            return {"reply": "这是合成测试回复", "reply_source": "graph", "rag_state": "RAG_DISABLED"}

    monkeypatch.setattr(chat_routes, "get_advisor_graph", lambda: StubGraph())
    payload = {"session_id": "client-chosen-test", "scene": "gaokao", "message": "请介绍专业"}
    for _ in range(2):
        response = client.post("/api/v1/chat", json=payload, headers=headers(token))
        assert response.status_code == 200, response.text
        assert '"type": "done"' in response.text
    assert seen[0]["session_id"] != seen[1]["session_id"]
    assert seen[0]["user_memory"] == seen[1]["user_memory"]
    assert seen[0]["user_memory"][0]["value"] == "concise"

    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    assert seen[-1]["user_memory"] == []
    assert client.get("/api/v1/memory/entries", headers=headers(token)).json()["count"] == 1


def test_invalid_memory_credential_fails_before_sse(client_db, monkeypatch):
    client, _ = client_db
    monkeypatch.setattr(chat_routes, "get_advisor_graph", lambda: (_ for _ in ()).throw(AssertionError()))
    response = client.post(
        "/api/v1/chat",
        json={"session_id": "fake-session", "scene": "gaokao", "message": "test"},
        headers=headers("mem1_" + "0" * 64),
    )
    assert response.status_code == 401
    assert "event: " not in response.text


def test_prompt_context_is_bounded_untrusted_data_not_evidence():
    items = [
        {"key": "preferred_regions", "value": "深圳"},
        {"key": "score", "value": "750"},
        {"key": "response_style", "value": "concise"},
    ]
    prompt = service.prompt_context(items)
    assert "深圳" in prompt
    assert "concise" in prompt
    assert '"score"' not in prompt
    assert "不可信输入" in prompt
    assert service.prompt_context([]) == ""


def test_bounded_expiry_prune_physically_deletes_only_expired(client_db):
    client, factory = client_db
    token = create_token(client)
    assert put(client, token, key="preferred_regions", value="广东").status_code == 200
    assert put(client, token, key="career_goal", value="软件工程").status_code == 200
    assert put(client, token, key="study_goal", value="提高数学").status_code == 200
    with factory() as db:
        for row in db.query(UserMemoryEntry).filter(
            UserMemoryEntry.memory_key.in_(["preferred_regions", "career_goal"])
        ):
            row.expires_at = _utcnow() - timedelta(days=1)
        db.commit()
    assert service.prune_expired(limit=1)["expired_entries_deleted"] == 1
    assert service.prune_expired(limit=1)["expired_entries_deleted"] == 1
    assert service.prune_expired(limit=1)["expired_entries_deleted"] == 0
    with factory() as db:
        assert [row.memory_key for row in db.query(UserMemoryEntry).all()] == ["study_goal"]
    with pytest.raises(service.MemoryInvalid):
        service.prune_expired(limit=0)


def test_llm_stream_receives_bounded_user_memory_without_token(monkeypatch):
    from types import SimpleNamespace

    from server.graph.nodes import llm_node as llm

    captured = []

    def fake_create(**kwargs):
        captured.append(kwargs)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(llm, "_get_llm_client", lambda: client)
    monkeypatch.setattr(
        llm, "_get_config", lambda: {"model": "synthetic-test-model", "temperature": 0, "max_tokens": 50}
    )
    monkeypatch.setattr(llm, "_load_system_prompt", lambda: "base prompt")
    monkeypatch.setattr(llm, "_load_memory_for_session", lambda _session: [])
    monkeypatch.setattr(llm, "build_llm_context", lambda _state: "current user question")
    assert list(
        llm.llm_node_stream(
            {
                "session_id": "session-one",
                "user_memory": [{"key": "preferred_regions", "value": "广州", "version": 1}],
            }
        )
    ) == [("ok", False)]
    message_text = "\n".join(item["content"] for item in captured[0]["messages"])
    assert "广州" in message_text
    assert all("广州" not in item["content"] for item in captured[0]["messages"] if item["role"] == "system")
    assert captured[0]["messages"][-1]["role"] == "user"
    assert "不可信输入" in message_text
    assert "mem1_" not in message_text
    assert list(llm.llm_node_stream({"session_id": "session-two"})) == [("ok", False)]
    assert all("广州" not in item["content"] for item in captured[1]["messages"])


def test_storage_error_does_not_log_secret_or_sql_details(client_db, monkeypatch, caplog):
    client, _ = client_db

    def fail():
        raise RuntimeError("user-memory-secret-canary")

    monkeypatch.setattr(service, "get_session", fail)
    with caplog.at_level("ERROR"):
        response = client.post("/api/v1/memory/vault")
    assert response.status_code == 503
    assert "user-memory-secret-canary" not in caplog.text + response.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "", "unknown"])
def test_memory_opt_in_cannot_enable_plaintext_vault_outside_explicit_dev(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("USER_MEMORY_ENABLED", "true")
    assert service.enabled() is False
    with pytest.raises(service.MemoryDisabled):
        service.create_vault()


def test_mutation_holds_credential_write_lock_in_sqlite(client_db):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    client, factory = client_db
    token = create_token(client)
    with factory() as first, factory() as second:
        owner = service._owner(first, token, write=True)
        second.execute(text("PRAGMA busy_timeout=0"))
        with pytest.raises(OperationalError, match="locked"):
            second.execute(
                text("UPDATE user_memory_vaults SET token_hash=token_hash WHERE id=:id"),
                {"id": owner.id},
            )
        second.rollback()
        first.rollback()
    rotated = service.rotate_token(token)["memory_token"]
    assert put(client, token).status_code == 401
    assert put(client, rotated).status_code == 200


def test_rotated_credential_cannot_delete_or_erase_vault(client_db):
    client, _ = client_db
    token = create_token(client)
    assert put(client, token).status_code == 200
    current = service.rotate_token(token)["memory_token"]
    assert client.delete("/api/v1/memory/entries/preferred_regions", headers=headers(token)).status_code == 401
    assert client.delete("/api/v1/memory/vault", headers=headers(token)).status_code == 401
    assert client.post("/api/v1/memory/vault/rotate", headers=headers(token)).status_code == 401
    assert client.get("/api/v1/memory/entries", headers=headers(current)).json()["count"] == 1


def test_expired_stale_update_leaves_row_untouched(client_db):
    client, factory = client_db
    token = create_token(client)
    assert put(client, token).status_code == 200
    with factory() as db:
        row = db.query(UserMemoryEntry).one()
        row.expires_at = _utcnow() - timedelta(seconds=1)
        db.commit()
    assert put(client, token, version=1).status_code == 409
    with factory() as db:
        assert db.query(UserMemoryEntry).one().value == "华南"
    assert put(client, token, value="重新确认", version=0).status_code == 200


@pytest.mark.parametrize("remove", ["delete", "prune"])
def test_recreated_entry_never_reuses_a_prior_revision(client_db, remove):
    client, factory = client_db
    token = create_token(client)
    old = put(client, token).json()["version"]
    if remove == "delete":
        assert client.delete("/api/v1/memory/entries/preferred_regions", headers=headers(token)).status_code == 200
    else:
        with factory() as db:
            db.query(UserMemoryEntry).one().expires_at = _utcnow() - timedelta(seconds=1)
            db.commit()
        assert service.prune_expired()["expired_entries_deleted"] == 1
    new = put(client, token, value="华北").json()["version"]
    assert new > old
    assert put(client, token, value="过时写入", version=old).status_code == 409
    assert client.get("/api/v1/memory/entries", headers=headers(token)).json()["items"][0]["value"] == "华北"


def test_revision_survives_rotation_and_is_shared_across_keys(client_db):
    client, _ = client_db
    token = create_token(client)
    first = put(client, token).json()["version"]
    second = put(client, token, key="study_goal", value="数学").json()["version"]
    current = service.rotate_token(token)["memory_token"]
    third = put(client, current, version=first).json()["version"]
    assert (first, second, third) == (1, 2, 3)


def test_revision_exhaustion_fails_closed(client_db):
    client, factory = client_db
    token = create_token(client)
    first = put(client, token).json()["version"]
    with factory() as db:
        db.query(UserMemoryVault).one().last_version = service.MAX_MEMORY_VERSION
        db.commit()
    assert put(client, token, version=first, value="不应保存").status_code == 409
    assert client.get("/api/v1/memory/entries", headers=headers(token)).json()["items"][0]["value"] == "华南"
