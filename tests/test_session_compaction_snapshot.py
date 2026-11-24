"""No-provider SQL/compaction tests: reuse, CAS, edits, expiry and deletion."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import Conversation, ConversationMessage, SessionCompactionSnapshot, _utcnow
from server.agent.durable_chat_context import build_durable_chat_context
from server.agent.session_compaction_store import (
    SessionCompactionStore,
    history_digest,
    policy_digest,
)

POLICY = policy_digest(model_id="test-model", keep_recent_rounds=2, summary_trigger_tokens=1)


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()


def _seed(factory, session_id: str = "session-alpha", *, rounds: int = 5):
    with factory() as db:
        conv = Conversation(session_id=session_id)
        db.add(conv)
        db.flush()
        for i in range(rounds):
            db.add(ConversationMessage(conversation_id=conv.id, role="user", content=f"Q{i}=" + "x" * 50))
            db.add(ConversationMessage(conversation_id=conv.id, role="assistant", content=f"A{i}=" + "y" * 50))
        db.commit()


def _history(factory, session_id: str = "session-alpha"):
    with factory() as db:
        conv = db.query(Conversation).filter(Conversation.session_id == session_id).one()
        return [{"role": m.role, "content": m.content} for m in conv.messages]


@pytest.mark.asyncio
async def test_first_summary_published_and_same_history_reused_without_model(db_factory):
    _seed(db_factory)
    store = SessionCompactionStore(db_factory)
    summarize = AsyncMock(return_value="旧对话线索：广东，不能代替 SQL 来源")
    params = {
        "session_id": "session-alpha",
        "history": _history(db_factory),
        "system_prompt": "POLICY",
        "current_prompt": "CURRENT evidence-id=src-100",
        "store": store,
        "policy_sha256": POLICY,
        "summarizer": summarize,
        "keep_recent_rounds": 2,
        "summary_trigger_tokens": 1,
    }

    first = await build_durable_chat_context(**params)
    second = await build_durable_chat_context(**params)
    assert first.status == "SNAPSHOT_PUBLISHED"
    assert second.status == "SNAPSHOT_REUSED"
    summarize.assert_awaited_once()
    assert first.messages[-1]["content"] == "CURRENT evidence-id=src-100"
    assert second.messages[1]["role"] == "user"
    assert "未经核验" in second.messages[1]["content"]
    with db_factory() as db:
        row = db.query(SessionCompactionSnapshot).one()
        assert row.version == 1
        assert row.covered_count == 6
        assert row.summary == "旧对话线索：广东，不能代替 SQL 来源"


@pytest.mark.asyncio
async def test_delta_is_kept_verbatim_until_incremental_threshold(db_factory):
    _seed(db_factory)
    store = SessionCompactionStore(db_factory)
    summarize = AsyncMock(side_effect=["原摘要", "更新摘要"])
    options = dict(
        session_id="session-alpha",
        system_prompt="POLICY",
        current_prompt="CURRENT",
        store=store,
        policy_sha256=POLICY,
        summarizer=summarize,
        keep_recent_rounds=2,
        summary_trigger_tokens=1,
        incremental_trigger_tokens=100,
    )
    assert (await build_durable_chat_context(history=_history(db_factory), **options)).status == "SNAPSHOT_PUBLISHED"

    with db_factory() as db:
        conv = db.query(Conversation).filter(Conversation.session_id == "session-alpha").one()
        db.add(ConversationMessage(conversation_id=conv.id, role="user", content="new user delta"))
        db.add(ConversationMessage(conversation_id=conv.id, role="assistant", content="new answer delta"))
        db.commit()
    reused = await build_durable_chat_context(history=_history(db_factory), **options)
    assert reused.status == "SNAPSHOT_REUSED_WITH_DELTA"
    assert "new user delta" not in str(reused.messages[1])
    assert "Q3=" in str(reused.messages)
    assert summarize.await_count == 1

    # Advance enough to include a large new delta in the older region.
    with db_factory() as db:
        conv = db.query(Conversation).filter(Conversation.session_id == "session-alpha").one()
        db.add(ConversationMessage(conversation_id=conv.id, role="user", content="BIG" + "x" * 500))
        db.add(ConversationMessage(conversation_id=conv.id, role="assistant", content="BIG" + "y" * 500))
        db.commit()
    refreshed = await build_durable_chat_context(history=_history(db_factory), **options)
    assert refreshed.status == "SNAPSHOT_PUBLISHED"
    assert summarize.await_count == 2
    assert "上一版未经核验" in str(summarize.await_args.args[0])
    assert store.load("session-alpha", _history(db_factory), policy_sha256=POLICY).version == 2


def test_cas_and_owner_isolation_and_policy_changes(db_factory):
    _seed(db_factory)
    _seed(db_factory, "session-beta")
    store = SessionCompactionStore(db_factory)
    history = _history(db_factory)
    assert store.publish(
        "session-alpha",
        history,
        covered_count=6,
        summary="summary A",
        policy_sha256=POLICY,
        expected_version=0,
    )
    assert not store.publish(
        "session-alpha",
        history,
        covered_count=6,
        summary="stale overwrite",
        policy_sha256=POLICY,
        expected_version=0,
    )
    assert store.load("session-beta", _history(db_factory, "session-beta"), policy_sha256=POLICY) is None
    assert store.load("session-alpha", history, policy_sha256="b" * 64) is None
    assert not store.publish(
        "session-alpha",
        history,
        covered_count=8,
        summary="bad CAS",
        policy_sha256=POLICY,
        expected_version=42,
    )
    assert store.load("session-alpha", history, policy_sha256=POLICY).summary == "summary A"


def test_modified_history_invalidates_cache_and_allows_versioned_replacement(db_factory):
    _seed(db_factory)
    store = SessionCompactionStore(db_factory)
    initial = _history(db_factory)
    assert store.publish(
        "session-alpha", initial, covered_count=6, summary="old summary",
        policy_sha256=POLICY,
    )
    with db_factory() as db:
        conv = db.query(Conversation).filter(Conversation.session_id == "session-alpha").one()
        conv.messages[0].content = "corrected historical question"
        db.commit()
    changed = _history(db_factory)
    assert history_digest(changed[:6]) != history_digest(initial[:6])
    assert store.load("session-alpha", changed, policy_sha256=POLICY) is None
    assert store.publish(
        "session-alpha", changed, covered_count=6, summary="corrected summary",
        policy_sha256=POLICY,
    )
    updated = store.load("session-alpha", changed, policy_sha256=POLICY)
    assert (updated.summary, updated.version) == ("corrected summary", 2)


def test_ttl_expiration_physical_prune_and_conversation_deletion(db_factory):
    _seed(db_factory)
    store = SessionCompactionStore(db_factory)
    history = _history(db_factory)
    assert store.publish("session-alpha", history, covered_count=6, summary="temporary", policy_sha256=POLICY)
    with db_factory() as db:
        row = db.query(SessionCompactionSnapshot).one()
        row.expires_at = _utcnow() - timedelta(seconds=1)
        db.commit()
    assert store.load("session-alpha", history, policy_sha256=POLICY) is None
    assert store.prune_expired() == 1
    assert store.prune_expired() == 0
    assert store.publish("session-alpha", history, covered_count=6, summary="after expiry", policy_sha256=POLICY)
    with db_factory() as db:
        conv = db.query(Conversation).filter(Conversation.session_id == "session-alpha").one()
        db.delete(conv)
        db.commit()
        assert db.query(SessionCompactionSnapshot).count() == 0


def test_orphan_or_invalid_session_cannot_publish(db_factory):
    _seed(db_factory)
    store = SessionCompactionStore(db_factory)
    history = _history(db_factory)
    assert not store.publish("session-none", history, covered_count=6, summary="orphan", policy_sha256=POLICY)
    with pytest.raises(ValueError, match="session identity"):
        store.load("../cross-owner", history, policy_sha256=POLICY)
