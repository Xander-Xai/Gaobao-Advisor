"""Opt-in skill disclosure contract: safety is mandatory, selection is explicit."""

import pytest

from skills.service import SkillAssetUnavailableError, SkillContextBudgetExceeded, SkillService


def _service(tmp_path):
    path = tmp_path / "gaokao"
    path.mkdir()
    for name, content in {
        "safety_rules": "SAFE",
        "mental_models": "MODEL",
        "heuristics": "HEURISTIC",
        "anti_patterns": "NEGATIVE",
    }.items():
        (path / f"{name}.md").write_text(content, encoding="utf-8")
    return SkillService(skills_dir=tmp_path)


def test_safe_only_by_default(tmp_path):
    assert _service(tmp_path).build_disclosed_context("gaokao") == "SAFE"


def test_one_selected_skill_without_unrelated_sections(tmp_path):
    text = _service(tmp_path).build_disclosed_context("gaokao", ["heuristics"])
    assert "SAFE" in text and "HEURISTIC" in text
    assert "MODEL" not in text and "NEGATIVE" not in text


def test_reject_unknown_duplicate_and_cross_scene(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(ValueError):
        svc.build_disclosed_context("gaokao", ["shell"])
    with pytest.raises(ValueError):
        svc.build_disclosed_context("general", ["heuristics"])
    with pytest.raises(ValueError):
        svc.build_disclosed_context("gaokao", ["heuristics", "heuristics"])
    with pytest.raises(ValueError):
        svc.build_disclosed_context("gaokao", [{"unhashable": "payload"}])


def test_budget_never_silently_drops_safety(tmp_path):
    with pytest.raises(SkillContextBudgetExceeded):
        _service(tmp_path).build_disclosed_context("gaokao", max_chars=2)


def test_missing_safety_fails_closed(tmp_path):
    svc = _service(tmp_path)
    (tmp_path / "gaokao" / "safety_rules.md").write_text("", encoding="utf-8")
    with pytest.raises(SkillAssetUnavailableError):
        svc.build_disclosed_context("gaokao")
