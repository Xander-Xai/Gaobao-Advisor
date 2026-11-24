"""Opt-in offline bridge from reviewed synthetic deny rules to Skills context.

Only server-approved, allowlisted rule IDs are ever rendered; neither a model
nor a saved user message can provide executable instructions. This module is
NOT wired to public /chat. The caller must authenticate the owner before use.
"""

from __future__ import annotations

from collections.abc import Sequence

from server.agent.synthetic_negative_rule_memory import (
    ALLOWED_RULES,
    SyntheticNegativeRuleMemory,
)
from skills.service import Scene, SkillContextBudgetExceeded, SkillService

_STATIC_TEXT = {
    "DENY_UNVERIFIED_ADMISSIONS_SOURCE": "只使用已核实的招生来源；否则明确拒答并指出缺失证据。",
    "DENY_ADMISSION_PROBABILITY_CLAIMS": "不得把历史分数线推断成个人录取概率。",
    "REQUIRE_REVIEW_ON_SQL_CONFLICT": "结构化数据存在矛盾时停止推荐并要求人工复核。",
}


def build_reviewed_synthetic_context(
    skill_service: SkillService,
    rule_store: SyntheticNegativeRuleMemory,
    *,
    owner: str,
    scene: str = Scene.GAOKAO,
    sections: Sequence[str] = (),
    max_chars: int = 4000,
) -> str:
    """Include only reviewed, unexpired, owner-scoped policy IDs.

    The operation has no model calls or external I/O. It does not implement
    public identity verification, Prompt Caching or production long-term memory.
    """
    if scene != Scene.GAOKAO:
        raise ValueError("Synthetic admissions negative rules require GAOKAO scene")
    base = skill_service.build_disclosed_context(scene, tuple(sections), max_chars=max_chars)
    rules = rule_store.active_rules(owner=owner)
    if any(rule not in ALLOWED_RULES or rule not in _STATIC_TEXT for rule in rules):
        raise ValueError("Unrecognized reviewed rule")
    if not rules:
        return base
    policy = "[SERVER_REVIEWED_SYNTHETIC_NEGATIVE_RULES]\n" + "\n".join("- " + _STATIC_TEXT[rule] for rule in rules)
    merged = base + "\n\n" + policy
    if len(merged) > max_chars:
        raise SkillContextBudgetExceeded("Reviewed context exceeds safe budget")
    return merged
