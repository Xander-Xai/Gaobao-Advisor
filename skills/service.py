"""Skill methodology service — loads skill files and builds strategy/context."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Scene(str, Enum):
    """Supported conversation scenes."""

    GAOKAO = "gaokao"
    KAO_YAN = "kaoyan"
    CAREER = "career"
    GENERAL = "general"


# Maximum number of missing fields to display in prompts
_MAX_DISPLAY_FIELDS = 3


class SkillStrategy(BaseModel):
    """Structured strategy output for a scene."""

    scene: str
    heuristics: list[str] = Field(default_factory=list)
    output_style: list[str] = Field(default_factory=list)
    answer_rules: list[str] = Field(default_factory=list)


# Default skill files to load per scene
_SKILL_FILES = [
    "mental_models.md",
    "heuristics.md",
    "anti_patterns.md",
    "safety_rules.md",
]

# Assets whose absence must NOT degrade silently. If any of these is missing the
# service refuses to answer rather than emitting an unguarded completion.
REQUIRED_SKILL_FILES = ("safety_rules.md",)

# Strategy content — PUBLIC DEMO EDITION.
# The upstream private edition carried a persona-mimicry style engine and a
# named individual's signature methodology. Those are removed here on rights
# grounds; what remains is generic education-advisory technique only.
_GAOKAO_HEURISTICS = [
    "先问清约束条件，再给方案。",
    "看分布中位情况，不用个别极端案例代表整体。",
    "从目标岗位倒推所需的院校与专业能力。",
    "先评估可承受的试错空间，再谈风险偏好。",
    "同档次下比较城市与区域的资源和机会密度。",
    "优先考虑难以被短期替代的能力组合。",
]

_GAOKAO_OUTPUT_STYLE = [
    "直接，先结论后展开。",
    "先讲事实边界，再讲可选方案。",
    "不夸大、不施压、不制造焦虑。",
    "不使用模仿特定人物的口吻、句式或口头禅。",
    "不重复套用固定开场模板。",
]

_GAOKAO_ANSWER_RULES = [
    "学校、专业、就业、政策类问题一律以查询返回的数据为准。",
    "结论必须明确，不输出正确的废话。",
    "不能用极端成功样本替普通个体做决策。",
    "没有确认过的精确数字，宁可说明数据缺失，也不能编造。",
    "数据必须标注来源与年份。",
    "每次推荐末尾必须附免责声明。",
    "本次部署使用合成演示数据，必须在答案中说明数据为演示用途。",
]


class SkillContextBudgetExceeded(ValueError):
    """Mandatory safety content cannot fit within the requested context budget."""


class SkillAssetUnavailableError(RuntimeError):
    """Raised when a mandatory safety asset is absent.

    The public demo deliberately fails closed: a missing ``safety_rules.md``
    must disable answer generation rather than silently produce an answer with
    no safety constraints applied.
    """


class SkillService:
    """Load skill markdown files and build strategy/context.

    Fail-closed policy: ``load_assets`` raises
    :class:`SkillAssetUnavailableError` if the directory or any file listed in
    :data:`REQUIRED_SKILL_FILES` is missing or empty. Optional methodology
    assets may be absent; safety assets may not.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or Path(__file__).parent
        self._loaded = False
        self._mental_models = ""
        self._heuristics_text = ""
        self._anti_patterns = ""
        self._safety_rules = ""

    def load_assets(self) -> None:
        """Load all skill markdown files. Idempotent.

        Raises:
            SkillAssetUnavailableError: if a mandatory safety asset is missing.
        """
        if self._loaded:
            return

        gaokao_dir = self._skills_dir / "gaokao"
        if not gaokao_dir.exists():
            # Do NOT mark as loaded: a failed safety check must re-raise on
            # every subsequent call rather than being memoised as success.
            self._assert_required_present(gaokao_dir)
            self._loaded = True
            return

        self._mental_models = self._read(gaokao_dir / "mental_models.md")
        self._heuristics_text = self._read(gaokao_dir / "heuristics.md")
        self._anti_patterns = self._read(gaokao_dir / "anti_patterns.md")
        self._safety_rules = self._read(gaokao_dir / "safety_rules.md")
        self._assert_required_present(gaokao_dir)
        self._loaded = True

    @staticmethod
    def _assert_required_present(gaokao_dir: Path) -> None:
        for filename in REQUIRED_SKILL_FILES:
            path = gaokao_dir / filename
            if not path.exists():
                raise SkillAssetUnavailableError(
                    f"mandatory safety asset missing: {path}. Answer generation is disabled (fail-closed)."
                )
            if not path.read_text(encoding="utf-8").strip():
                raise SkillAssetUnavailableError(
                    f"mandatory safety asset is empty: {path}. Answer generation is disabled (fail-closed)."
                )

    def _read(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def build_strategy(self, scene: str) -> SkillStrategy:
        """Build a structured strategy for the given scene."""
        self.load_assets()
        if scene == Scene.GAOKAO:
            return SkillStrategy(
                scene=scene,
                heuristics=_GAOKAO_HEURISTICS,
                output_style=_GAOKAO_OUTPUT_STYLE,
                answer_rules=_GAOKAO_ANSWER_RULES,
            )
        return SkillStrategy(scene=scene)

    def build_context(self, scene: str) -> str:
        """Build a context string to inject into the LLM system message."""
        self.load_assets()

        parts: list[str] = []

        if self._safety_rules:
            parts.append(self._safety_rules)

        if scene == Scene.GAOKAO:
            if self._mental_models:
                parts.append(self._mental_models)
            if self._heuristics_text:
                parts.append(self._heuristics_text)
            if self._anti_patterns:
                parts.append(self._anti_patterns)
        else:
            if self._mental_models:
                parts.append(self._mental_models)

        # Fail-closed guarantee: load_assets() already raised if safety rules
        # were absent, so `parts` always contains them here. Keep an explicit
        # assertion so a future refactor cannot silently reintroduce the
        # unguarded path.
        assert self._safety_rules, "safety rules must be loaded (fail-closed)"
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_question_reply(self, scene: str, missing_fields: list[str]) -> str:
        """Build a natural-language question reply for missing fields."""
        self.load_assets()

        field_labels = {
            "province": "省份",
            "score": "分数",
            "subject": "选科",
            "interest": "专业兴趣",
            "region": "地域偏好",
            "family": "家庭资源",
            "goal": "核心诉求",
        }

        lead = {
            Scene.GAOKAO: "你先别急着让我直接报学校。",
            Scene.KAO_YAN: "你先别急着定考研还是就业。",
            Scene.CAREER: "你先别急着换方向。",
        }.get(scene, "你先别急着下结论。")

        labels = [field_labels.get(f, f) for f in missing_fields[:_MAX_DISPLAY_FIELDS]]
        if not labels:
            return f"{lead} 你先把情况说说，我帮你看看。"

        missing_text = "、".join(labels)
        return (
            f"{lead} "
            f"关键条件还差{missing_text}，这些没锁准我不会给你拍脑袋的答案。"
            f"你先把{labels[0]}告诉我，我再往下走。"
        )

    def build_disclosed_context(
        self,
        scene: str,
        sections: tuple[str, ...] | list[str] = (),
        *,
        max_chars: int = 4000,
    ) -> str:
        """Disclose only requested domain sections while always including safety.

        This bounds prompt disclosure, NOT disk I/O or the online tool registry.
        Opt-in only; it does not change the existing build_context() behavior.
        """
        self.load_assets()
        if type(max_chars) is not int or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        if not isinstance(sections, (tuple, list)):
            raise ValueError("sections must be a list or tuple")
        allowed = {
            "mental_models": self._mental_models,
            "heuristics": self._heuristics_text,
            "anti_patterns": self._anti_patterns,
        }
        if scene != Scene.GAOKAO and sections:
            raise ValueError("Domain skills cannot be disclosed to unrelated scenes")
        if any(not isinstance(name, str) or name not in allowed for name in sections):
            raise ValueError("Unknown skill section")
        if len(set(sections)) != len(sections):
            raise ValueError("Duplicate skill section")
        parts = [self._safety_rules]
        parts.extend(allowed[name] for name in sections if allowed[name].strip())
        content = "\n\n---\n\n".join(parts)
        if len(content) > max_chars:
            raise SkillContextBudgetExceeded("Safety-preserving context exceeds budget")
        return content
