"""Deep Agents orchestration for an authorized admissions research task.

A model plans and delegates to two deterministic, SQL-backed LangGraph
subagents. It sees only coverage and opaque receipts, not raw admissions
records. The host builds the final evidence from independently re-read SQL.
No public chat endpoint, shell, database write or personal profile access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_ROWS = 100
MAX_ANALYSIS_ATTEMPTS = 2
NO_HISTORY_REASON = "no_admission_history"
RECOVERABLE_ANALYSIS_ERRORS = frozenset({"oracle_mismatch", "invalid_analysis_output", "execution_failed"})
MODEL_RE = re.compile(r"openai:[A-Za-z0-9_.-]{2,100}")
_SKILL = """---
name: admissions-research
description: Research two approved schools and a major using historical admissions and enrollment plans.
---
Use write_todos to plan the comparison. Delegate history to admission-history and
plans to enrollment-plans. Never invent records, subject requirements or probabilities.
Submit both opaque receipt IDs with submit_research after both subagents succeed.
Coverage gaps are evidence, not a reason to silently omit a school or year.
"""
_SYSTEM = (
    "You coordinate an approved admissions research task, not a recommendation. "
    "Read the admissions-research skill and use write_todos. Delegate both named "
    "subagents with task; their fixed SQL scope cannot be changed. "
    "Submit both opaque receipt IDs using submit_research. Final prose is not evidence. "
    "Do not request arbitrary SQL, external searches, personal data, or code outside the explicitly offered sandbox."
)


@dataclass(frozen=True)
class ResearchScope:
    school_ids: tuple[int, int]
    major_id: int
    province: str
    subject_type: str
    batch: str
    year_from: int
    year_to: int

    def __post_init__(self):
        if (
            len(self.school_ids) != 2
            or len(set(self.school_ids)) != 2
            or any(type(x) is not int or x < 1 for x in (*self.school_ids, self.major_id))
        ):
            raise ValueError("Two distinct schools and one major are required")
        if any(
            not isinstance(x, str) or not 1 <= len(x.strip()) <= limit
            for x, limit in ((self.province, 20), (self.subject_type, 10), (self.batch, 20))
        ):
            raise ValueError("Invalid admissions cohort")
        if (
            type(self.year_from) is not int
            or type(self.year_to) is not int
            or not 2000 <= self.year_from <= self.year_to <= 2100
            or self.year_to - self.year_from > 4
        ):
            raise ValueError("Research at most five years")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def read_research_rows(session: Any, scope: ResearchScope, kind: str) -> list[dict[str, Any]]:
    """Fixed SQL projection; the agent cannot change predicates or table names."""
    from sqlalchemy import or_, select

    from db.models import AdmissionScore, EnrollmentPlan, School

    if kind == "admission-history":
        table = AdmissionScore
        fields = ("id", "school_id", "major_id", "year", "min_score", "max_score", "min_rank")
    elif kind == "enrollment-plans":
        table = EnrollmentPlan
        fields = ("id", "school_id", "major_id", "year", "plan_count", "subject_requirement", "tuition", "duration")
    else:
        raise ValueError("Unknown research dimension")
    query = (
        select(*(getattr(table, key) for key in fields))
        .select_from(table)
        .join(School, School.id == table.school_id)
        .where(
            table.school_id.in_(scope.school_ids),
            table.major_id == scope.major_id,
            table.province == scope.province,
            table.batch == scope.batch,
            table.year.between(scope.year_from, scope.year_to),
            or_(School.special_type.is_(None), School.special_type != "SYNTHETIC_DEMO_DATA"),
        )
    )
    if kind == "admission-history":
        query = query.where(AdmissionScore.subject_type == scope.subject_type)
    rows = [
        dict(row)
        for row in session.execute(query.order_by(table.school_id, table.year, table.id).limit(MAX_ROWS + 1)).mappings()
    ]
    if len(rows) > MAX_ROWS:
        raise ValueError("Research scope exceeds row budget")
    return rows


def _coverage(rows: list[dict[str, Any]], scope: ResearchScope, kind: str) -> dict[str, Any]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["school_id"], row["year"]), []).append(row)
    missing = []
    ambiguous = []
    incomplete = []
    for school in scope.school_ids:
        for year in range(scope.year_from, scope.year_to + 1):
            group = grouped.get((school, year), [])
            if not group:
                missing.append([school, year])
            elif len(group) != 1:
                ambiguous.append([school, year])
            elif kind == "admission-history" and (group[0]["min_score"] is None or group[0]["min_rank"] is None):
                incomplete.append([school, year])
            elif kind == "enrollment-plans" and (group[0]["plan_count"] is None or not group[0]["subject_requirement"]):
                incomplete.append([school, year])
    return {"missing": missing, "ambiguous": ambiguous, "incomplete": incomplete}


def build_research_subgraph(name: str, collect: Callable[[str], str]):
    """A deterministic specialist: real LangGraph delegation, no extra model calls."""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    def query(_state: MessagesState) -> dict:
        return {"messages": [AIMessage(content=collect(name))]}

    graph = StateGraph(MessagesState)
    graph.add_node("query_approved_scope", query)
    graph.add_edge(START, "query_approved_scope")
    graph.add_edge("query_approved_scope", END)
    return graph.compile()


def build_deep_agent(model_id: str, tools: list, subagents: list, *, lease_guard=None):
    """Optional SDK construction. Restrict default tools but keep task/todos."""
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from deepagents.backends import StateBackend
    from deepagents.middleware import FilesystemMiddleware
    from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, TodoListMiddleware
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver

    register_harness_profile(
        model_id,
        HarnessProfile(
            excluded_tools=frozenset({"ls", "glob", "grep", "write_file", "edit_file", "delete", "execute"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    backend = StateBackend()
    model = ChatOpenAI(
        model=model_id.removeprefix("openai:"),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or None,
        temperature=0,
        max_tokens=1800 if any(tool.__name__ == "analyze_history" for tool in tools) else 600,
        timeout=20,
        max_retries=0,
    )
    # The agent calls the model through LangChain, so the provider request is
    # made by the framework. The chat model itself is kept; only its provider
    # calls are routed through the LLM Resource Governor.
    from server.agent.governed_chat_model import govern_chat_model

    model = govern_chat_model(
        model,
        provider=os.getenv("GAOBAO_LLM_RESEARCH_PROVIDER", "openai").strip() or "openai",
        operation="research",
        model_name=model_id.removeprefix("openai:"),
    )

    class LeaseMiddleware(AgentMiddleware):
        def before_model(self, state, runtime):
            if lease_guard is not None:
                lease_guard()
            return None

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=_SYSTEM,
        backend=backend,
        middleware=[
            FilesystemMiddleware(backend=backend, tools=["read_file"]),
            TodoListMiddleware(),
            LeaseMiddleware(),
            ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ],
        skills=["/skills/"],
        checkpointer=InMemorySaver(),
        subagents=subagents,
    )


def run_admissions_research(
    scope: ResearchScope,
    session_factory: Callable,
    model_id: str,
    *,
    approval_ref: str,
    authorized: bool = False,
    model_authorized: bool = False,
    agent_factory: Callable = build_deep_agent,
    analysis_image_id: str | None = None,
    lease_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Private operator entrypoint. Only validated SQL evidence may be returned."""
    if authorized is not True or model_authorized is not True:
        raise ValueError("Explicit data and model authorization required")
    if not MODEL_RE.fullmatch(model_id) or not 1 <= len(approval_ref.strip()) <= 200:
        raise ValueError("Invalid model or approval reference")
    task_id = "research_" + uuid.uuid4().hex
    lock = threading.Lock()
    snapshots: dict[str, list[dict[str, Any]]] = {}
    receipts: dict[str, str] = {}
    attempted: set[str] = set()
    submitted = False
    analysis_receipt: dict[str, Any] | None = None
    analysis_attempts = 0
    analysis_running = False
    analysis_attempt_log: list[dict[str, Any]] = []
    if analysis_image_id is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", analysis_image_id):
        raise ValueError("Invalid immutable sandbox image")
    kinds = ("admission-history", "enrollment-plans")

    def guard() -> None:
        if lease_guard is not None:
            lease_guard()

    def collect(kind: str) -> str:
        guard()
        with lock:
            if kind not in kinds or kind in attempted or submitted:
                raise ValueError("Unapproved or repeated research delegation")
            attempted.add(kind)
        with session_factory() as session:
            rows = read_research_rows(session, scope, kind)
        guard()
        coverage = _coverage(rows, scope, kind)
        ref = _digest({"task": task_id, "scope": asdict(scope), "kind": kind, "rows": rows})
        with lock:
            snapshots[kind] = rows
            receipts[kind] = ref
        # Only bounded counts and opaque receipt IDs reach the external model.
        return _canonical(
            {
                "kind": kind,
                "receipt": ref,
                "row_count": len(rows),
                "missing_count": len(coverage["missing"]),
                "ambiguous_count": len(coverage["ambiguous"]),
                "incomplete_count": len(coverage["incomplete"]),
            }
        )

    def analyze_history(code: str) -> str:
        """Run generated Python in the sandbox against ONLY the delegated history snapshot.

        Read /input/rows.json; group by school_id and year, emit the exact
        descriptive statistics schema specified in the system instructions.
        """
        nonlocal analysis_attempts, analysis_running, analysis_receipt
        guard()
        from server.agent.admissions_analysis_sandbox import Scope, execute_admissions_analysis

        with lock:
            if (
                analysis_image_id is None
                or analysis_running
                or submitted
                or kinds[0] not in snapshots
                or analysis_attempts >= MAX_ANALYSIS_ATTEMPTS
                or (
                    analysis_attempts > 0
                    and (
                        analysis_receipt is None
                        or analysis_receipt.get("status") != "BLOCKED"
                        or analysis_receipt.get("cleanup_verified") is not True
                        or analysis_receipt.get("error") not in RECOVERABLE_ANALYSIS_ERRORS
                    )
                )
            ):
                raise ValueError("Sandbox execution or repair not authorized")
            analysis_attempts += 1
            analysis_running = True
            rows = [dict(row) for row in snapshots[kinds[0]]]
        try:
            sandbox_scope = Scope(
                scope.province, scope.subject_type, scope.batch, scope.year_from, scope.year_to, "major"
            )
            result = execute_admissions_analysis(
                rows,
                sandbox_scope,
                ("school_id", "year"),
                code.encode(),
                image_id=analysis_image_id,
                approval_ref=approval_ref,
                authorized=True,
            )
            guard()
            with lock:
                analysis_receipt = result
                retry_allowed = (
                    result.get("status") == "BLOCKED"
                    and result.get("cleanup_verified") is True
                    and result.get("error") in RECOVERABLE_ANALYSIS_ERRORS
                    and analysis_attempts < MAX_ANALYSIS_ATTEMPTS
                )
                observation = {
                    "status": result["status"],
                    "result_sha256": result.get("result_sha256"),
                    "error": result.get("error"),
                    "attempt": analysis_attempts,
                    "retry_allowed": retry_allowed,
                }
                analysis_attempt_log.append(observation)
            return _canonical(observation)
        finally:
            with lock:
                analysis_running = False

    def submit_research(history_receipt: str, plans_receipt: str) -> str:
        """Submit exactly the two returned opaque receipt IDs after delegation."""
        nonlocal submitted
        guard()
        with lock:
            if (
                submitted
                or set(receipts) != set(kinds)
                or (history_receipt != receipts[kinds[0]] or plans_receipt != receipts[kinds[1]])
            ):
                raise ValueError("Research evidence incomplete or invalid")
            if analysis_image_id is not None and (
                analysis_receipt is None or analysis_receipt.get("status") != "VERIFIED_DESCRIPTIVE_ANALYSIS"
            ):
                raise ValueError("Verified sandbox analysis required")
            submitted = True
        return "RESEARCH_RECEIPTS_ACCEPTED"

    subagents = [
        {
            "name": kind,
            "description": f"Read the approved {kind} SQL cohort and return coverage and receipt only.",
            "runnable": build_research_subgraph(kind, collect),
        }
        for kind in kinds
    ]
    report: dict[str, Any] = {
        "schema": "admissions-research/v1",
        "task_id": task_id,
        "scope": asdict(scope),
        "approval_ref_sha256": _digest(approval_ref),
        "status": "BLOCKED",
        "admission_recommendation_verified": False,
        "engine": "deepagents" if agent_factory is build_deep_agent else "injected_test_controller",
        "provider_billing_verified": False,
    }
    try:
        # An empty cohort cannot enter the descriptive sandbox oracle.
        # Reject before constructing or invoking a paid model.
        if analysis_image_id is not None:
            guard()
            with session_factory() as session:
                history = read_research_rows(session, scope, "admission-history")
            guard()
            if not history:
                report.update(
                    engine="deterministic_preflight",
                    error=NO_HISTORY_REASON,
                    provider_usage=None,
                    observed_model_messages=0,
                )
                return report
        tools = [submit_research] if analysis_image_id is None else [submit_research, analyze_history]
        guard()
        agent = (
            build_deep_agent(model_id, tools, subagents, lease_guard=lease_guard)
            if agent_factory is build_deep_agent
            else agent_factory(model_id, tools, subagents)
        )
        if agent_factory is build_deep_agent:
            from deepagents.backends.utils import create_file_data

            file_data = create_file_data(_SKILL)
        else:
            file_data = {"content": [_SKILL]}
        outcome = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Complete both approved research dimensions. "
                            + (
                                "After the history delegation call analyze_history with Python code. "
                                "Read /input/rows.json (rows and group_by); group by school_id and year; "
                                "sort groups by key. Print one JSON object with groups, each containing key, "
                                "records, missing_min_scores, median_min_score, lowest_min_score, "
                                "highest_min_score, best_min_rank, worst_min_rank. "
                                "Score metrics use non-null min_score, rank metrics non-null min_rank. "
                                "Use statistics.median; unavailable metrics are null. "
                                "If retry_allowed is true, revise the code once using the error code; "
                                "never change the approved data scope. Only submit after sandbox verification."
                                if analysis_image_id is not None
                                else ""
                            )
                        ),
                    }
                ],
                "files": {"/skills/admissions-research/SKILL.md": file_data},
            },
            config={"configurable": {"thread_id": task_id}, "recursion_limit": 64},
        )
        guard()
        messages = outcome.get("messages", []) if isinstance(outcome, dict) else []
        usage = [getattr(msg, "usage_metadata", None) for msg in messages if getattr(msg, "type", "") == "ai"]
        report["observed_model_messages"] = len(usage)
        report["provider_usage"] = (
            {key: sum(v[key] for v in usage) for key in ("input_tokens", "output_tokens")}
            if usage
            and all(
                isinstance(v, dict)
                and all(type(v.get(key)) is int and v[key] >= 0 for key in ("input_tokens", "output_tokens"))
                for v in usage
            )
            else None
        )
        if not submitted:
            report["error"] = "unverified_finish"
            return report
        with session_factory() as session:
            fresh = {kind: read_research_rows(session, scope, kind) for kind in kinds}
        guard()
        if any(fresh[kind] != snapshots[kind] for kind in kinds):
            report["error"] = "source_changed"
            return report
        coverage = {kind: _coverage(fresh[kind], scope, kind) for kind in kinds}
        partial = any(gaps for dimension in coverage.values() for gaps in dimension.values())
        report.update(
            {
                "status": "VERIFIED_PARTIAL_RESEARCH" if partial else "VERIFIED_RESEARCH",
                "coverage": coverage,
                "evidence": fresh,
                "receipt_ids": receipts,
                "evidence_sha256": _digest(fresh),
                "sandbox_analysis": analysis_receipt,
            }
        )
    except Exception as exc:
        report["error"] = "research_failed"
        report["error_type"] = type(exc).__name__
    finally:
        report["delegated_dimensions"] = sorted(snapshots)
        report["sandbox_attempts"] = analysis_attempt_log
    return report


def render_verified_research(report: dict[str, Any]) -> str:
    """Render the in-process receipt, not arbitrary model prose or a public recommendation."""
    if report.get("schema") != "admissions-research/v1" or report.get("status") not in {
        "VERIFIED_RESEARCH",
        "VERIFIED_PARTIAL_RESEARCH",
    }:
        raise ValueError("Only verified research can be rendered")
    evidence = report["evidence"]
    kinds = ("admission-history", "enrollment-plans")
    if set(evidence) != set(kinds) or _digest(evidence) != report.get("evidence_sha256"):
        raise ValueError("Research evidence digest mismatch")
    scope = ResearchScope(**report["scope"])
    coverage = {kind: _coverage(evidence[kind], scope, kind) for kind in kinds}
    if coverage != report["coverage"]:
        raise ValueError("Research coverage mismatch")
    partial = any(gaps for dimension in coverage.values() for gaps in dimension.values())
    if partial != (report["status"] == "VERIFIED_PARTIAL_RESEARCH"):
        raise ValueError("Research status mismatch")

    def row_text(row: Any) -> str:
        # JSON encoding keeps embedded newlines and terminal controls in data escaped.
        return _canonical(row).replace("\u001b", "\\u001b")

    lines = [
        "高考志愿历史数据研究报告（内部，非录取预测）",
        "任务状态: " + report["status"],
        "任务 ID: " + row_text(report["task_id"]),
        "研究范围: " + row_text(report["scope"]),
        "证据 SHA256: " + report["evidence_sha256"],
        "数据仅为授权数据库中的历史记录；未独立证明来源真实性或当前有效性。",
        "招生计划表没有科类字段；选科要求原文保留，不代表已验证考生符合条件。",
    ]
    for kind, title in (("admission-history", "历史录取分数与位次"), ("enrollment-plans", "招生计划")):
        lines.extend(["", title, "覆盖缺口: " + row_text(coverage[kind])])
        lines.extend(row_text(row) for row in evidence[kind])
    analysis = report.get("sandbox_analysis")
    if analysis is not None:
        from server.agent.admissions_analysis_sandbox import _digest as analysis_digest
        from server.agent.admissions_analysis_sandbox import _json as analysis_json

        if (
            analysis.get("status") != "VERIFIED_DESCRIPTIVE_ANALYSIS"
            or analysis.get("cleanup_verified") is not True
            or analysis_digest(analysis_json(analysis["result"])) != analysis.get("result_sha256")
            or analysis_digest(
                analysis_json({"rows": evidence["admission-history"], "group_by": ("school_id", "year")})
            )
            != analysis.get("input_sha256")
        ):
            raise ValueError("Sandbox evidence mismatch")
        lines.extend(["", "独立核验的沙箱描述性统计", row_text(analysis["result"])])
    lines.extend(["", "不得将缺失数据、历史分数或统计结果解释为录取概率、志愿推荐或资格认证。"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-data", action="store_true", required=True)
    parser.add_argument("--authorize-model-call", action="store_true", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--school-ids", type=int, nargs=2, required=True)
    parser.add_argument("--major-id", type=int, required=True)
    parser.add_argument("--province", required=True)
    parser.add_argument("--subject-type", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--year-from", type=int, required=True)
    parser.add_argument("--year-to", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--sandbox-image", help="Opt-in immutable local image for verified analysis")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, help="Optional private verified research text report")
    args = parser.parse_args(argv)
    if os.getenv("ADMISSIONS_RESEARCH_ENABLED", "").lower() == "true":
        parser.error("Managed research requires an approved journal run; use admissions_research_taskrun or dispatch")
    if args.report_out is not None and (args.report_out.exists() or args.report_out.resolve() == args.out.resolve()):
        parser.error("Report output must be a new, separate path")
    if not os.getenv("DATABASE_URL") or not os.getenv("OPENAI_API_KEY"):
        parser.error("Explicit authorized database and model credentials required")
    scope = ResearchScope(
        tuple(args.school_ids),
        args.major_id,
        args.province,
        args.subject_type,
        args.batch,
        args.year_from,
        args.year_to,
    )
    from db.database import get_session

    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        report = run_admissions_research(
            scope,
            get_session,
            args.model,
            approval_ref=args.approval_ref,
            authorized=args.authorized_data,
            model_authorized=args.authorize_model_call,
            analysis_image_id=args.sandbox_image,
        )
        handle.write(_canonical(report) + "\n")
    if report["status"].startswith("VERIFIED_"):
        if args.report_out is not None:
            rendered = render_verified_research(report)
            fd = os.open(args.report_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
