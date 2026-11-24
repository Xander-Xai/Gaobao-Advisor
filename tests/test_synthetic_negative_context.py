"""Isolated synthetic-only integration of approved negative rules and Skills."""

import pytest

from server.agent.synthetic_negative_context import build_reviewed_synthetic_context
from server.agent.synthetic_negative_rule_memory import (
    SyntheticNegativeRuleMemory,
)
from skills.service import SkillContextBudgetExceeded, SkillService

RULE = "REQUIRE_REVIEW_ON_SQL_CONFLICT"
SOURCE = "syn_" + "b" * 32


def _env(tmp_path, tick):
    folder = tmp_path / "skills" / "gaokao"
    folder.mkdir(parents=True)
    for name, content in {
        "safety_rules": "MANDATORY-SAFETY",
        "heuristics": "ADVICE",
        "mental_models": "MODEL",
        "anti_patterns": "NEGATIVE",
    }.items():
        (folder / (name + ".md")).write_text(content, encoding="utf-8")
    skills = SkillService(skills_dir=tmp_path / "skills")
    path = tmp_path / "negative.sqlite3"
    store = SyntheticNegativeRuleMemory(path, clock=lambda: tick[0])
    return skills, store, path


def _propose(store):
    store.propose(
        owner="tenant-demo-owner",
        rule_key=RULE,
        source_run_id=SOURCE,
        proposed_by="agent-proposer",
        ttl_seconds=5,
    )


def _review(store):
    store.review(
        owner="tenant-demo-owner",
        rule_key=RULE,
        reviewed_by="reviewer-admin",
        approve=True,
        reviewer_authorized=True,
    )


def test_only_reviewed_rules_appear_across_store_instances(tmp_path):
    tick = [1000]
    skills, store, path = _env(tmp_path, tick)
    _propose(store)
    candidate = build_reviewed_synthetic_context(skills, store, owner="tenant-demo-owner", sections=["heuristics"])
    assert candidate == "MANDATORY-SAFETY\n\n---\n\nADVICE"
    _review(store)
    reopened = SyntheticNegativeRuleMemory(path, clock=lambda: tick[0])
    approved = build_reviewed_synthetic_context(skills, reopened, owner="tenant-demo-owner", sections=["heuristics"])
    assert "MANDATORY-SAFETY" in approved
    assert "数据存在矛盾" in approved
    assert "MODEL" not in approved and "\n\n---\n\nNEGATIVE" not in approved
    assert approved == build_reviewed_synthetic_context(
        skills, reopened, owner="tenant-demo-owner", sections=["heuristics"]
    )
    assert "SERVER_REVIEWED" not in build_reviewed_synthetic_context(
        skills, reopened, owner="another-owner", sections=[]
    )


def test_expired_rule_is_removed_from_context(tmp_path):
    tick = [1000]
    skills, store, _ = _env(tmp_path, tick)
    _propose(store)
    _review(store)
    tick[0] = 1005
    result = build_reviewed_synthetic_context(skills, store, owner="tenant-demo-owner")
    assert result == "MANDATORY-SAFETY"


def test_context_budget_and_wrong_scene_fail_closed(tmp_path):
    tick = [1000]
    skills, store, _ = _env(tmp_path, tick)
    _propose(store)
    _review(store)
    with pytest.raises(SkillContextBudgetExceeded):
        build_reviewed_synthetic_context(skills, store, owner="tenant-demo-owner", max_chars=16)
    with pytest.raises(ValueError):
        build_reviewed_synthetic_context(skills, store, owner="tenant-demo-owner", scene="career")
