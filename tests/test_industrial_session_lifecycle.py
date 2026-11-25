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
