"""Authenticated session-scoped deletion of live application records.

The HTTP route authenticates ownership before invoking this function. This
scope excludes backup replicas, immutable external audit logs and provider
records; do not claim a global right-to-erasure completion.
"""

from __future__ import annotations

from typing import Any

from server.report.storage import ReportStorage


def erase_live_session_data(session_id: str, reports: ReportStorage) -> dict[str, Any]:
    """Stage report files, commit related SQL deletes, then delete hidden files.

    Staged files are restored only after a confirmed pre-commit rollback.
    If commit is attempted but its outcome is unknown, or final file cleanup
    fails, retain the hidden stage for operator reconciliation.
    """
    from db.database import get_session
    from db.models import (
        ChatTurnReceipt,
        Conversation,
        ConversationMessage,
        Feedback,
        Highlight,
        QualityScore,
        SessionCompactionSnapshot,
    )

    staged = reports.stage_session_deletion(session_id)
    try:
        db = get_session()
    except Exception:
        if staged is not None:
            reports.restore_staged_session(session_id, staged[0])
        raise
    removed: dict[str, int] = {}
    commit_attempted = False
    try:
        # Serialize with industrial chat finalization before deleting children.
        session_row = db.query(Conversation).filter(Conversation.session_id == session_id).with_for_update().first()
        removed["quality_scores"] = (
            db.query(QualityScore).filter(QualityScore.conversation_id == session_id).delete(synchronize_session=False)
        )
        removed["feedback"] = (
            db.query(Feedback).filter(Feedback.session_id == session_id).delete(synchronize_session=False)
        )
        removed["highlights"] = (
            db.query(Highlight).filter(Highlight.session_id == session_id).delete(synchronize_session=False)
        )
        if session_row is None:
            removed["chat_turn_receipts"] = 0
            removed["compaction_snapshots"] = 0
            removed["messages"] = 0
            removed["conversations"] = 0
        else:
            # Bulk SQL deletes bypass ORM cascades; explicitly erase the summary
            # even when SQLite foreign-key enforcement is disabled.
            removed["chat_turn_receipts"] = (
                db.query(ChatTurnReceipt)
                .filter(ChatTurnReceipt.conversation_id == session_row.id)
                .delete(synchronize_session=False)
            )
            removed["compaction_snapshots"] = (
                db.query(SessionCompactionSnapshot)
                .filter(SessionCompactionSnapshot.conversation_id == session_row.id)
                .delete(synchronize_session=False)
            )
            removed["messages"] = (
                db.query(ConversationMessage)
                .filter(ConversationMessage.conversation_id == session_row.id)
                .delete(synchronize_session=False)
            )
            removed["conversations"] = (
                db.query(Conversation).filter(Conversation.id == session_row.id).delete(synchronize_session=False)
            )
        commit_attempted = True
        db.commit()
    except Exception:
        db.rollback()
        # A connection loss during COMMIT may occur after the database has
        # committed. Never restore private reports based solely on an error.
        if staged is not None and not commit_attempted:
            reports.restore_staged_session(session_id, staged[0])
        raise
    finally:
        db.close()
    removed["reports"] = reports.finalize_staged_session(staged[0]) if staged else 0
    return {
        "live_data_erased": True,
        "deleted_row_counts": removed,
        "session_data_scope": "APPLICATION_DB_AND_LOCAL_JSON_REPORTS_ONLY",
        "backups_and_external_processors_erased": False,
        "concurrent_session_recreation_prevented": False,
        "privacy_review_required_for_production": True,
    }
