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

    Staged files are restored on SQL failure. If final file cleanup fails after
    SQL commit, the API reports an explicit incomplete status; the staging
    directory remains inaccessible to ordinary report lookup for recovery.
    """
    from db.database import get_session
    from db.models import Conversation, ConversationMessage, Feedback, Highlight, QualityScore

    staged = reports.stage_session_deletion(session_id)
    db = get_session()
    removed: dict[str, int] = {}
    try:
        removed["quality_scores"] = db.query(QualityScore).filter(
            QualityScore.conversation_id == session_id
        ).delete(synchronize_session=False)
        removed["feedback"] = db.query(Feedback).filter(
            Feedback.session_id == session_id
        ).delete(synchronize_session=False)
        removed["highlights"] = db.query(Highlight).filter(
            Highlight.session_id == session_id
        ).delete(synchronize_session=False)
        session_row = db.query(Conversation).filter(Conversation.session_id == session_id).first()
        if session_row is None:
            removed["messages"] = 0
            removed["conversations"] = 0
        else:
            removed["messages"] = db.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == session_row.id
            ).delete(synchronize_session=False)
            removed["conversations"] = db.query(Conversation).filter(
                Conversation.id == session_row.id
            ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        if staged is not None:
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
