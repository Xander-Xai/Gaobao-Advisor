"""Operator triage never exposes rejected drafts or certifies facts."""

from __future__ import annotations

import json

from db.models import OperationAuditLog
from tests.conftest import auth_header, login


def _create_ticket(factory, ticket: str = "a" * 32) -> str:
    db = factory()
    try:
        db.add(
            OperationAuditLog(
                actor="chat-finalizer",
                role="service",
                action="chat.review_required",
                resource_type="chat_review",
                resource_id=ticket,
                outcome="PENDING",
                detail_json=json.dumps(
                    {
                        "reason_codes": ["JUDGE_REJECTED"],
                        "delivered_reply_sha256": "b" * 64,
                        "rewrite_attempts": 1,
                        "data_profile": "SYNTHETIC_DEMO",
                    }
                ),
            )
        )
        db.commit()
    finally:
        db.close()
    return ticket


def test_review_queue_restricts_roles_and_exposes_only_metadata(admin_client, admin_factory):
    ticket = _create_ticket(admin_factory)
    viewer = login(admin_client, "viewer")
    operator = login(admin_client, "operator")
    assert admin_client.get("/api/v1/admin/chat-reviews", headers=auth_header(viewer)).status_code == 403

    response = admin_client.get("/api/v1/admin/chat-reviews", headers=auth_header(operator))
    assert response.status_code == 200, response.text
    item = next(x for x in response.json()["items"] if x["ticket_id"] == ticket)
    assert item["triage_status"] == "PENDING"
    assert item["reason_codes"] == ["JUDGE_REJECTED"]
    assert item["delivered_reply_sha256"] == "b" * 64
    assert response.json()["factual_verification"] == "NOT_PERFORMED"
    for forbidden in ("input_text", "reply", "session_id", "profile", "prompt", "original_draft"):
        assert forbidden not in item


def test_only_admin_can_append_review_triage_without_approval(admin_client, admin_factory):
    ticket = _create_ticket(admin_factory, "c" * 32)
    operator = login(admin_client, "operator")
    admin = login(admin_client, "admin")
    payload = {"verdict": "escalated", "review_reference": "synthetic-case-001"}
    path = f"/api/v1/admin/chat-reviews/{ticket}/triage"
    assert admin_client.post(path, json=payload, headers=auth_header(operator)).status_code == 403
    result = admin_client.post(path, json=payload, headers=auth_header(admin))
    assert result.status_code == 200, result.text
    assert result.json()["triage_status"] == "ESCALATED"
    assert result.json()["factual_verification"] == "NOT_PERFORMED"
    assert admin_client.post(path, json=payload, headers=auth_header(admin)).status_code == 409

    listed = admin_client.get("/api/v1/admin/chat-reviews", headers=auth_header(operator))
    item = next(x for x in listed.json()["items"] if x["ticket_id"] == ticket)
    assert item["triage_status"] == "ESCALATED"


def test_review_triage_rejects_unknown_ticket(admin_client):
    admin = login(admin_client, "admin")
    result = admin_client.post(
        "/api/v1/admin/chat-reviews/" + "f" * 32 + "/triage",
        json={"verdict": "acknowledged", "review_reference": "synthetic-case-002"},
        headers=auth_header(admin),
    )
    assert result.status_code == 404



def test_review_ticket_and_delivered_message_share_transaction(admin_factory, monkeypatch):
    from db.models import Conversation, ConversationMessage, QualityScore
    from server.graph.nodes import memory as memory_module

    monkeypatch.setattr(memory_module, "get_session", admin_factory)
    session_id = "synthetic-review-transaction"
    question = "合成测试用户的问题"
    answer = "无法核验此合成数据，请到官方渠道确认。"
    result = memory_module.memory_node(
        {
            "session_id": session_id,
            "input_text": question,
            "reply": answer,
            "slots": {"province": "示例省·甲"},
            "atomic_memory": True,
            "quality_scored": False,
            "review_required": True,
            "review_reason_codes": ["JUDGE_UNAVAILABLE"],
            "rewrite_attempts": 1,
        }
    )
    assert result["trace"][-1]["event"] == "persisted"
    assert result["review_ticket_id"]

    db = admin_factory()
    try:
        conversation = db.query(Conversation).filter_by(session_id=session_id).one()
        msgs = db.query(ConversationMessage).filter_by(conversation_id=conversation.id).all()
        assert [(m.role, m.content) for m in msgs] == [("user", question), ("assistant", answer)]
        audit = (
            db.query(OperationAuditLog)
            .filter_by(action="chat.review_required", resource_id=result["review_ticket_id"])
            .one()
        )
        assert "JUDGE_UNAVAILABLE" in audit.detail_json
        assert question not in audit.detail_json
        assert answer not in audit.detail_json
        assert session_id not in audit.detail_json
        assert db.query(QualityScore).filter_by(conversation_id=session_id).count() == 0
    finally:
        db.close()


def test_atomic_persistence_error_rolls_back_both_messages(admin_factory, monkeypatch):
    from db.models import Conversation, ConversationMessage, OperationAuditLog
    from server.graph.nodes import memory as memory_module

    monkeypatch.setattr(memory_module, "get_session", admin_factory)

    def fail_slots(*args, **kwargs):
        raise RuntimeError("synthetic database fault")

    monkeypatch.setattr(memory_module, "save_slots", fail_slots)
    session_id = "synthetic-rollback-turn"
    result = memory_module.memory_node(
        {
            "session_id": session_id,
            "input_text": "问题",
            "reply": "不得交付的失败答案",
            "atomic_memory": True,
            "review_required": True,
            "review_reason_codes": ["UNATTRIBUTED_NUMBER"],
        }
    )
    assert result["trace"][-1]["event"] == "persist_failed"
    assert "review_ticket_id" not in result
    db = admin_factory()
    try:
        conversation = db.query(Conversation).filter_by(session_id=session_id).one()
        assert db.query(ConversationMessage).filter_by(conversation_id=conversation.id).count() == 0
        assert db.query(OperationAuditLog).filter_by(action="chat.review_required").count() == 0
    finally:
        db.close()
