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
