"""Evidence-bound synthetic decision card: actual SQL, not invented admissions advice.

The bounded planner chooses tools from observations. It NEVER estimates
admission odds, recommends a school, or uses a real student's information.
The final card is composed from captured tool outputs, not free-form LLM text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from server.agent.readonly_harness import (
    Decision,
    HarnessLimits,
    HarnessResult,
    Planner,
    ReadOnlyTool,
    run_readonly_agent,
)
from server.agent.readonly_tools import _require_demo_database, synthetic_readonly_tools

PROFILE = "SYNTHETIC_DEMO"
CONTROLLER = "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
DISCLAIMER = "虚构演示数据：仅展示证据和约束检查，不得用于真实志愿填报、录取预测或院校推荐。"


def _record_source(kind: str, record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"synthetic:{kind}:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def _fetch_row(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Read the exact seeded school-major-province-year row, never fuzzy match."""
    from db.database import get_session
    from db.models import AdmissionScore, EnrollmentPlan, Major, School

    _require_demo_database()
    school_name, major_name = args["school_name"], args["major_name"]
    province, year = args["province"], args["year"]
    if "（合成）" not in school_name or "（合成）" not in major_name:
        raise ValueError("Only marked fictional subjects allowed")
    if province not in {"示例省·甲", "示例省·乙", "示例省·丙", "示例省·丁"}:
        raise ValueError("Only fictional provinces allowed")
    if type(year) is not int or not 2020 <= year <= 2100:
        raise ValueError("Invalid synthetic year")
    db = get_session()
    try:
        school = db.query(School).filter(School.name == school_name).one_or_none()
        major = db.query(Major).filter(Major.name == major_name).one_or_none()
        if (
            school is None
            or major is None
            or school.special_type != "SYNTHETIC_DEMO_DATA"
            or school.data_source_note != "合成演示数据，无真实来源"
        ):
            raise ValueError("Missing verified synthetic entity")
        model = AdmissionScore if kind == "admission" else EnrollmentPlan
        rows = (
            db.query(model)
            .filter(
                model.school_id == school.id,
                model.major_id == major.id,
                model.province == province,
                model.year == year,
            )
            .order_by(model.id)
            .limit(2)
            .all()
        )
        if len(rows) != 1:
            # 0 rows is missing evidence; 2 rows is ambiguous evidence.
            return {
                "data_profile": PROFILE,
                "record_count": len(rows),
                "source_ids": [],
                "school_name": school_name,
                "major_name": major_name,
                "province": province,
                "year": year,
            }
        row = rows[0]
        details: dict[str, Any] = {
            "school_name": school_name,
            "major_name": major_name,
            "province": province,
            "year": year,
        }
        if kind == "admission":
            details.update(
                subject_type=row.subject_type,
                min_score=row.min_score,
                min_rank=row.min_rank,
                batch=row.batch,
            )
        else:
            details.update(
                subject_requirement=row.subject_requirement,
                plan_count=row.plan_count,
                batch=row.batch,
            )
        fingerprint = {"table": kind, "row_id": row.id, **details}
        return {
            "data_profile": PROFILE,
            "record_count": 1,
            "source_ids": [_record_source(kind, fingerprint)],
            **details,
        }
    finally:
        db.close()


@dataclass(frozen=True)
class DecisionCardProfile:
    school_a: str
    school_b: str
    major: str
    province: str
    score: int
    subject_type: str
    year: int


class DecisionCardPlanner:
    """Observation-aware policy for five real read-only lookups, not a model."""

    def __init__(self, profile: DecisionCardProfile) -> None:
        self.profile = profile

    def decide(self, task: str, history: list[dict[str, Any]], tool_schemas: dict[str, dict[str, str]]) -> Decision:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for event in history:
            try:
                row = json.loads(event["observation"])
                key = (event["tool"], row.get("school_name", row.get("major_name", "")))
            except (KeyError, TypeError, ValueError):
                return Decision("abstain")
            if row.get("data_profile") != PROFILE or not isinstance(row.get("source_ids"), list):
                return Decision("abstain")
            seen[key] = row
        plan: list[tuple[str, str, dict[str, Any]]] = [
            ("major_lookup", self.profile.major, {"major_name": self.profile.major}),
        ]
        for school in (self.profile.school_a, self.profile.school_b):
            plan.append(
                (
                    "admission_lookup",
                    school,
                    {
                        "school_name": school,
                        "major_name": self.profile.major,
                        "province": self.profile.province,
                        "year": self.profile.year,
                    },
                )
            )
        for school in (self.profile.school_a, self.profile.school_b):
            plan.append(
                (
                    "plan_lookup",
                    school,
                    {
                        "school_name": school,
                        "major_name": self.profile.major,
                        "province": self.profile.province,
                        "year": self.profile.year,
                    },
                )
            )
        for tool, name, args in plan:
            if (tool, name) not in seen:
                return Decision("tool", tool=tool, arguments=args)
        if any(
            seen[(tool, name)].get("record_count") != 1 or len(seen[(tool, name)]["source_ids"]) != 1
            for tool, name, _ in plan
        ):
            return Decision("abstain")
        refs = [seen[(tool, name)]["source_ids"][0] for tool, name, _ in plan]
        return Decision("finish", answer=json.dumps({"synthetic_demo": True, "sources": refs}), evidence_ids=refs)


def build_synthetic_decision_card(
    profile: DecisionCardProfile,
    planner: Planner | None = None,
    *,
    require_provider_usage: bool = False,
) -> dict[str, Any]:
    """Compose a bounded, source-linked research card from actual database rows."""
    if profile.school_a == profile.school_b or profile.subject_type not in {"物理类", "历史类"}:
        raise ValueError("Invalid fictional decision-card profile")
    tools = synthetic_readonly_tools()

    def admission(args: dict[str, Any]) -> dict[str, Any]:
        return _fetch_row("admission", args)

    def plan(args: dict[str, Any]) -> dict[str, Any]:
        return _fetch_row("plan", args)

    tools = {
        "major_lookup": tools["major_lookup"],
        "admission_lookup": ReadOnlyTool(
            "admission_lookup",
            {"school_name": str, "major_name": str, "province": str, "year": int},
            admission,
        ),
        "plan_lookup": ReadOnlyTool(
            "plan_lookup",
            {"school_name": str, "major_name": str, "province": str, "year": int},
            plan,
        ),
    }
    captured: list[tuple[str, dict[str, Any]]] = []
    wrapped: dict[str, ReadOnlyTool] = {}
    for name, tool in tools.items():

        def make_handler(target: ReadOnlyTool, label: str):
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                observed = target.handler(args)
                captured.append((label, observed))
                return observed

            return handler

        wrapped[name] = ReadOnlyTool(name, tool.parameters, make_handler(tool, name))

    result = run_readonly_agent(
        "核验合成考生画像的专业门类、两校历年录取数据与对应招生计划，不作录取预测",
        planner if planner is not None else DecisionCardPlanner(profile),
        wrapped,
        HarnessLimits(
            max_model_steps=8 if planner is not None else 6,
            max_tool_calls=5,
            max_repeated_call=1,
            max_observation_chars=750,
            max_context_chars=4000,
            max_reported_tokens=7000,
            require_provider_usage=require_provider_usage,
        ),
    )
    return compose_synthetic_decision_card(
        profile,
        captured,
        result,
        planner_label=CONTROLLER if planner is None else "INJECTED_PLANNER_UNVERIFIED",
    )


def compose_synthetic_decision_card(
    profile: DecisionCardProfile,
    captured: list[tuple[str, dict[str, Any]]],
    result: HarnessResult,
    *,
    planner_label: str,
) -> dict[str, Any]:
    """One deterministic Evidence Gate for both built-in and Deep Agents backends."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for label, row in captured:
        key = (label, row.get("school_name", row.get("major_name", "")))
        records[key] = row
    schools: list[dict[str, Any]] = []
    missing: list[str] = []
    evidence: list[str] = []
    for school in (profile.school_a, profile.school_b):
        admission_row = records.get(("admission_lookup", school), {})
        plan_row = records.get(("plan_lookup", school), {})
        if admission_row.get("record_count") != 1:
            missing.append(f"{school}：缺少指定省份/专业/年份的合成录取记录")
        if plan_row.get("record_count") != 1:
            missing.append(f"{school}：缺少指定省份/专业/年份的合成招生计划")
        if admission_row.get("record_count") == 1 and (
            type(admission_row.get("min_score")) is not int
            or type(admission_row.get("min_rank")) is not int
            or admission_row["min_rank"] <= 0
        ):
            missing.append(f"{school}：最低分或最低位次缺少可信的合成记录")
        if plan_row.get("record_count") == 1 and (
            type(plan_row.get("plan_count")) is not int or plan_row["plan_count"] <= 0
        ):
            missing.append(f"{school}：招生计划人数为空或无效")
        required = plan_row.get("subject_requirement")
        constraint = "unknown"
        if required == "不限":
            constraint = "compatible"
        elif required == "物理必选":
            constraint = "compatible" if profile.subject_type == "物理类" else "incompatible"
        elif required is not None:
            missing.append(f"{school}：未知选科规则，需人工确认")
        if constraint == "incompatible":
            missing.append(f"{school}：选科与合成招生计划要求不匹配")
        school_sources = admission_row.get("source_ids", []) + plan_row.get("source_ids", [])
        evidence.extend(school_sources)
        historical_score = admission_row.get("min_score")
        schools.append(
            {
                "school_name": school,
                "historical_min_score": historical_score,
                "historical_min_rank": admission_row.get("min_rank"),
                "historical_subject_type": admission_row.get("subject_type"),
                "historical_score_difference": (
                    profile.score - historical_score if type(historical_score) is int else None
                ),
                "plan_count": plan_row.get("plan_count"),
                "plan_subject_requirement": required,
                "subject_constraint": constraint,
                "evidence_ids": school_sources,
            }
        )
        if admission_row.get("record_count") == 1 and admission_row.get("subject_type") != profile.subject_type:
            missing.append(f"{school}：录取历史记录选科类别与画像不一致")
    major = records.get(("major_lookup", profile.major), {})
    if major.get("record_count") != 1 or not isinstance(major.get("category"), str) or not major.get("category"):
        missing.append("缺少指定合成专业的学科门类来源")
    evidence.extend(major.get("source_ids", []))

    # A refusal is safe even without tools, but only *observed negative SQL
    # evidence* establishes completion of a negative research task.
    negative_evidence_count = sum(
        row.get("record_count") != 1
        or (
            label == "admission_lookup"
            and row.get("record_count") == 1
            and row.get("subject_type") != profile.subject_type
        )
        or (
            label == "plan_lookup"
            and row.get("record_count") == 1
            and (
                row.get("subject_requirement") not in {"不限", "物理必选"}
                or (row.get("subject_requirement") == "物理必选" and profile.subject_type != "物理类")
            )
        )
        for label, row in captured
    )
    # No success is possible on missing data, tool failure or fabricated claims.
    completed = (
        result.status == "completed"
        and len(captured) == 5
        and len(evidence) == 5
        and not missing
        and len(set(evidence)) == len(evidence)
        and all(row.get("data_profile") == PROFILE for _, row in captured)
    )
    if completed:
        declared = json.loads(result.answer)
        completed = (
            declared.get("synthetic_demo") is True
            and isinstance(declared.get("sources"), list)
            and len(declared["sources"]) == len(evidence)
            and set(declared["sources"]) == set(evidence)
        )
    if not completed:
        schools = []  # Never emit partial comparisons as complete advice.
    return {
        "status": "evidence_complete_historical_only" if completed else "needs_review",
        "reason": result.reason,
        "data_profile": PROFILE,
        "planner": planner_label,
        "profile": {
            "province": profile.province,
            "score": profile.score,
            "subject_type": profile.subject_type,
            "major": profile.major,
            "year": profile.year,
        },
        "major_category": major.get("category") if completed else None,
        "schools": schools,
        "missing_evidence": missing if not completed else [],
        "observed_negative_evidence_count": negative_evidence_count,
        "evidence_ids": evidence if completed else [],
        "manual_checks": [
            "真实考生位次不可由示例分数推断",
            "真实当年招生计划、选科要求及官方来源均未核验",
            "历史最低分差不是录取概率，不提供冲稳保推荐",
        ],
        "execution": result.public_report(),
        "disclaimer": DISCLAIMER,
    }
