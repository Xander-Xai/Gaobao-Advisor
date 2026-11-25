"""Quality orchestration node — runs the full quality pipeline."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from server.services.quality import QualityOrchestrator
from skills.service import SkillService

_orchestrator = None
_skill_service = None
_init_lock = threading.Lock()


def _get_orchestrator() -> QualityOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _init_lock:
            if _orchestrator is None:  # double-check
                _orchestrator = QualityOrchestrator()
    return _orchestrator


def _get_skill_service() -> SkillService:
    global _skill_service
    if _skill_service is None:
        with _init_lock:
            if _skill_service is None:  # double-check
                _skill_service = SkillService(skills_dir=Path(__file__).parents[3] / "skills")
    return _skill_service


def quality_orchestrate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Run pre-generation quality checks with cross-validation and AI-era risk.

    Detects emotion, selects cognitive models, recommends heuristics,
    loads contextual knowledge, builds skill context for the scene,
    and checks AI-era major risks. Post-SQL validation runs in validate_data.
    """
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        return {
            "trace": list(state.get("trace", [])) + [{"node": "quality_orchestrate", "event": "industrial_sql_only"}]
        }
    orch = _get_orchestrator()
    skill_svc = _get_skill_service()
    text = state.get("input_text", "")
    slots = state.get("slots", {})
    scene = state.get("scene", "general")

    # Emotion detection
    emotion_result = orch.detect_emotion(text)
    emotion_state = emotion_result.get("level", "normal")

    # Model selection
    model_result = orch.select_models(slots, text)
    cognitive_model = model_result.get("model", "default")

    # Decision heuristics
    heuristics_raw = orch.recommend_heuristics(slots)
    decision_heuristics = [
        h.get("description", h.get("name", "")) for h in (heuristics_raw if isinstance(heuristics_raw, list) else [])
    ]

    # Server-owned disclosure policy: factual lookups need safety rules only;
    # advisory tasks additionally need the decision methods and anti-patterns.
    skill_sections = ("heuristics", "anti_patterns") if scene == "gaokao" and not state.get("knowledge_query") else ()
    skill_context = skill_svc.build_disclosed_context(scene, skill_sections, max_chars=5500)

    # AI-era risk: check target/interested major when scene is gaokao
    major_risk: dict[str, Any] | None = None
    try:
        if scene == "gaokao":
            major = slots.get("target_major") or slots.get("interested_major") or slots.get("interest")
            if major:
                risk_result = orch.get_major_risk(major)
                if risk_result is not None:
                    major_risk = risk_result
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("AI-era risk check failed: %s", e)

    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": "quality_orchestrate",
            "event": f"emotion={emotion_state}",
            "model": cognitive_model,
            "skill_scene": scene,
            "skill_sections": list(skill_sections),
        }
    )

    # Analytics event logging
    try:
        from analytics import EventTracker

        tracker = EventTracker()
        session_id = state.get("session_id", "default")
        tracker.log_event(session_id, "query_submitted", {"input_text": text[:100], "scene": scene})
        tracker.log_event(session_id, "emotion_scored", {"label": emotion_state})
        if emotion_state == "negative":
            tracker.log_event(session_id, "emotion_detected", {"level": "crisis_detected"})
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("Analytics logging failed: %s", e)

    result = {
        "emotion_state": emotion_state,
        "cognitive_model": cognitive_model,
        "decision_heuristics": decision_heuristics,
        "skill_context": skill_context,
        "skill_sections": list(skill_sections),
        "trace": trace,
    }
    if major_risk is not None:
        result["major_risk"] = major_risk
    return result
