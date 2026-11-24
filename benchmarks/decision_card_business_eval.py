"""Reproducible synthetic business-task evaluation: deterministic baseline vs opt-in LLM.

Run without paid API access:
    python -m benchmarks.decision_card_business_eval --out /tmp/gaobao-business-baseline.json

Actual model evaluation requires an explicit model, key and authorization:
    python -m benchmarks.decision_card_business_eval --mode model \
        --model YOUR_MODEL_ID --permit-external-model --max-cases 8 \
        --out /tmp/gaobao-business-model.json

The suite is fixture-authored and independently checked against the synthetic
database; it is NOT a reviewed ground-truth set or real-world admissions test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.decision_card_trace_eval import TRACE_CONTRACT, grade_step_trace
from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.synthetic_decision_card import DecisionCardProfile, build_synthetic_decision_card

_SCHOOL_A = DEMO_SCHOOLS[0][0]
_SCHOOL_B = DEMO_SCHOOLS[6][0]
_SCHOOL_MISSING = DEMO_SCHOOLS[1][0]
_MAJOR = DEMO_MAJORS[0][0]

# Independently authored expectation labels: not learned from the runtime.
_CASES: tuple[tuple[str, str, str, str, str, int, str], ...] = (
    ("holdout-complete-a", "holdout", _SCHOOL_A, _SCHOOL_B, "示例省·甲", 550, "evidence_complete_historical_only"),
    ("holdout-complete-b", "holdout", _SCHOOL_B, _SCHOOL_A, "示例省·乙", 560, "evidence_complete_historical_only"),
    ("holdout-missing-plan", "holdout", _SCHOOL_A, _SCHOOL_B, "示例省·丙", 560, "needs_review"),
    ("holdout-no-school", "holdout", _SCHOOL_A, _SCHOOL_MISSING, "示例省·甲", 550, "needs_review"),
    ("develop-complete-a", "development", _SCHOOL_A, _SCHOOL_B, "示例省·甲", 520, "evidence_complete_historical_only"),
    ("develop-complete-b", "development", _SCHOOL_B, _SCHOOL_A, "示例省·乙", 610, "evidence_complete_historical_only"),
    ("develop-no-plan", "development", _SCHOOL_A, _SCHOOL_B, "示例省·丁", 520, "needs_review"),
    ("develop-missing", "development", _SCHOOL_MISSING, _SCHOOL_B, "示例省·乙", 530, "needs_review"),
)


def frozen_cases() -> list[dict[str, Any]]:
    """Cases are explicitly labeled fictional, with a stable suite digest."""
    out: list[dict[str, Any]] = []
    for case_id, split, school_a, school_b, province, score, expected in _CASES:
        item = {
            "case_id": case_id,
            "split": split,
            "profile": asdict(
                DecisionCardProfile(
                    school_a=school_a,
                    school_b=school_b,
                    major=_MAJOR,
                    province=province,
                    score=score,
                    subject_type="物理类",
                    year=DEMO_YEAR,
                )
            ),
            "expected_status": expected,
            "data_profile": "SYNTHETIC_DEMO",
        }
        out.append(item)
    return out


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def independent_sql_oracle(profile: DecisionCardProfile) -> tuple[str, dict[str, dict[str, Any]]]:
    """Verify expected case outcome and SQL fields without asking the planner."""
    from db.database import get_session
    from db.models import AdmissionScore, EnrollmentPlan, Major, School

    db = get_session()
    details: dict[str, dict[str, Any]] = {}
    try:
        major = db.query(Major).filter(Major.name == profile.major).one()
        fully_supported = True
        for name in (profile.school_a, profile.school_b):
            school = db.query(School).filter(School.name == name).one()
            admissions = (
                db.query(AdmissionScore)
                .filter(
                    AdmissionScore.school_id == school.id,
                    AdmissionScore.major_id == major.id,
                    AdmissionScore.province == profile.province,
                    AdmissionScore.year == profile.year,
                )
                .all()
            )
            plans = (
                db.query(EnrollmentPlan)
                .filter(
                    EnrollmentPlan.school_id == school.id,
                    EnrollmentPlan.major_id == major.id,
                    EnrollmentPlan.province == profile.province,
                    EnrollmentPlan.year == profile.year,
                )
                .all()
            )
            if len(admissions) != 1 or len(plans) != 1:
                fully_supported = False
                continue
            a, p = admissions[0], plans[0]
            compatible = p.subject_requirement == "不限" or (
                p.subject_requirement == "物理必选" and profile.subject_type == "物理类"
            )
            valid = (
                compatible
                and a.subject_type == profile.subject_type
                and type(a.min_score) is int
                and type(a.min_rank) is int
                and a.min_rank > 0
                and type(p.plan_count) is int
                and p.plan_count > 0
            )
            fully_supported = fully_supported and valid
            common = {
                "school_name": name,
                "major_name": profile.major,
                "province": profile.province,
                "year": profile.year,
            }
            admission_record = {
                "table": "admission",
                "row_id": a.id,
                **common,
                "subject_type": a.subject_type,
                "min_score": a.min_score,
                "min_rank": a.min_rank,
                "batch": a.batch,
            }
            plan_record = {
                "table": "plan",
                "row_id": p.id,
                **common,
                "subject_requirement": p.subject_requirement,
                "plan_count": p.plan_count,
                "batch": p.batch,
            }
            admission_digest = hashlib.sha256(
                json.dumps(admission_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:12]
            plan_digest = hashlib.sha256(
                json.dumps(plan_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:12]
            expected_sources = [f"synthetic:admission:{admission_digest}", f"synthetic:plan:{plan_digest}"]
            details[name] = {
                "evidence_ids": expected_sources,
                "historical_min_score": a.min_score,
                "historical_min_rank": a.min_rank,
                "plan_count": p.plan_count,
                "plan_subject_requirement": p.subject_requirement,
                "historical_subject_type": a.subject_type,
                "historical_score_difference": profile.score - a.min_score if a.min_score is not None else None,
            }
        return ("evidence_complete_historical_only" if fully_supported else "needs_review"), details
    finally:
        db.close()


def grade_business_case(case: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    profile = DecisionCardProfile(**case["profile"])
    expected, rows = independent_sql_oracle(profile)
    if expected != case["expected_status"]:
        raise ValueError("Fixture label disagrees with independently queried synthetic ground truth")
    errors: list[str] = []
    if card.get("status") != expected:
        errors.append("outcome_mismatch")
    if card.get("data_profile") != "SYNTHETIC_DEMO":
        errors.append("wrong_data_profile")
    if expected == "needs_review":
        if card.get("schools") or card.get("evidence_ids") or not card.get("missing_evidence"):
            errors.append("unsafe_partial_decision")
        # An early refusal can be correct if the SQL tools *actually found*
        # a missing record or contradictory subject rule. Blind abstention
        # without negative evidence must not be reported as task success.
        if card.get("observed_negative_evidence_count", 0) < 1:
            errors.append("negative_evidence_not_observed")
        if card.get("reason") != "explicit_abstention":
            errors.append("runtime_failure_not_valid_abstention")
    else:
        schools = card.get("schools")
        refs = card.get("evidence_ids")
        if not isinstance(schools, list) or len(schools) != 2 or not isinstance(refs, list) or len(refs) != 5:
            errors.append("incomplete_evidence")
        else:
            # Independent business identity check: two returned rows must cover
            # two distinct requested schools.  A duplicated school row could
            # otherwise pass every per-row field check while the top-level
            # source list still contains evidence for both schools.
            actual_names = [item.get("school_name") if isinstance(item, dict) else None for item in schools]
            if not all(isinstance(name, str) for name in actual_names) or set(actual_names) != {
                profile.school_a,
                profile.school_b,
            }:
                errors.append("school_identity_mismatch")
            for item in schools:
                if not isinstance(item, dict):
                    errors.append("invalid_school_record")
                    continue
                school_name = item.get("school_name")
                if not isinstance(school_name, str):
                    errors.append("invalid_school_identity")
                    continue
                true_row = rows.get(school_name)
                if true_row is None or any(item.get(field) != value for field, value in true_row.items()):
                    errors.append("factual_mismatch")
                    break
                if len(item.get("evidence_ids", [])) != 2:
                    errors.append("school_sources_invalid")
            expected_all = {f"synthetic:major:{hashlib.sha256(profile.major.encode()).hexdigest()[:12]}"}
            for oracle_row in rows.values():
                expected_all.update(oracle_row["evidence_ids"])
            if len(set(refs)) != 5 or set(refs) != expected_all:
                errors.append("source_contract_invalid")
    return {"passed": not errors, "failure_codes": sorted(set(errors))}


def run_cases(cases: list[dict[str, Any]], planner_factory: Any | None = None) -> dict[str, Any]:
    """Same cases and oracle for baseline or a supplied planner factory."""
    reports = []
    for case in cases:
        profile = DecisionCardProfile(**case["profile"])
        planner = planner_factory(profile) if planner_factory is not None else None
        start = time.monotonic()
        card = build_synthetic_decision_card(
            profile,
            planner=planner,
            require_provider_usage=planner_factory is not None,
        )
        assessment = grade_business_case(case, card)
        observed = card["execution"]
        try:
            trace_receipt = grade_step_trace(observed)
        except ValueError:
            trace_receipt = None  # Never copy malformed or raw provider/tool data.
        trace_failures = [] if trace_receipt is not None else ["trace_contract_invalid"]
        reports.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "passed": assessment["passed"] and trace_receipt is not None,
                "safe_abstain": (
                    case["expected_status"] == "needs_review"
                    and card["status"] == "needs_review"
                    and card["reason"] == "explicit_abstention"
                    and card.get("observed_negative_evidence_count", 0) >= 1
                    and not card["schools"]
                    and not card["evidence_ids"]
                ),
                "failure_codes": sorted(set(assessment["failure_codes"] + trace_failures)),
                "execution_status": observed["status"],
                "execution_reason": observed["reason"],
                "trace_receipt": trace_receipt,
                "status": card["status"],
                "reason": card["reason"],
                "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
                "tool_calls": observed["tool_calls"],
                "model_steps": observed["model_steps"],
                "prompt_tokens": observed["prompt_tokens"],
                "completion_tokens": observed["completion_tokens"],
                "tool_failures": observed["tool_failures"],
                "history_evictions": observed["history_evictions"],
            }
        )
    return {
        "dataset_profile": "SYNTHETIC_DEMO",
        "oracle": "SEPARATE_REAL_SQL_QUERY_NOT_MODEL_SELF_RATING",
        "human_review_complete": False,
        "dataset_sha256": _digest(cases),
        "cases": len(reports),
        "passed": sum(item["passed"] for item in reports),
        "trace_contract": TRACE_CONTRACT,
        "trace_validated_cases": sum(item["trace_receipt"] is not None for item in reports),
        "safe_abstentions": sum(item["safe_abstain"] for item in reports),
        "holdout_cases": sum(item["split"] == "holdout" for item in reports),
        "holdout_passed": sum(item["passed"] for item in reports if item["split"] == "holdout"),
        "total_tool_calls": sum(item["tool_calls"] for item in reports),
        "total_model_steps": sum(item["model_steps"] for item in reports),
        "provider_prompt_tokens": (
            sum(item["prompt_tokens"] for item in reports)
            if all(item["prompt_tokens"] is not None for item in reports)
            else None
        ),
        "provider_completion_tokens": (
            sum(item["completion_tokens"] for item in reports)
            if all(item["completion_tokens"] is not None for item in reports)
            else None
        ),
        "real_admissions_accuracy": "NOT_MEASURED",
        "results": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "model"), default="baseline")
    parser.add_argument("--model", help="Explicit OpenAI-compatible model ID")
    parser.add_argument(
        "--prompt-variant",
        choices=("baseline", "evidence_first"),
        default="baseline",
        help="Frozen prompt experiment arm; never alters SQL/tools or the business oracle",
    )
    parser.add_argument("--permit-external-model", action="store_true")
    parser.add_argument("--max-cases", type=int, default=8)
    parser.add_argument("--synthetic-canary-journal", type=Path)
    parser.add_argument("--canary-run-key", help="Opaque synthetic experiment allocation key")
    parser.add_argument("--canary-percentage", type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.max_cases <= 8:
        parser.error("--max-cases must be 1..8")
    canary_assignment = None
    if any(value is not None for value in (args.synthetic_canary_journal, args.canary_run_key, args.canary_percentage)):
        if args.mode != "model" or args.synthetic_canary_journal is None or args.canary_run_key is None:
            parser.error("Synthetic canary requires explicit model mode, journal and run key")
        if args.prompt_variant != "baseline":
            parser.error("Canary selects a frozen prompt; do not override the arm")
        if not args.permit_external_model or not args.model or not os.environ.get("OPENAI_API_KEY"):
            parser.error("Canary execution needs explicit model, key and paid model authorization")
        if os.getenv("APP_ENV", "").strip().lower() not in {"development", "test", "local"}:
            parser.error("Synthetic canary requires explicit non-production APP_ENV")
        if os.getenv("ENABLE_OUTBOUND_FETCH", "false").strip().lower() != "false":
            parser.error("Disable arbitrary outbound fetching in synthetic canary mode")
        if args.canary_percentage is not None and not 0 <= args.canary_percentage <= 100:
            parser.error("Canary allocation percentage must be 0..100")
        if not args.synthetic_canary_journal.is_file():
            parser.error("Canary journal must already exist")
        if args.out.exists() or args.out.is_symlink():
            parser.error("Canary output must be a fresh path")
        from server.agent.synthetic_canary import choose_synthetic_variant
        from server.agent.synthetic_optimization_registry import SyntheticOptimizationReviewStore
        from server.agent.taskrun_store import SyntheticTaskRunStore

        registry = SyntheticOptimizationReviewStore(SyntheticTaskRunStore(args.synthetic_canary_journal))
        percentage = 10 if args.canary_percentage is None else args.canary_percentage
        canary_assignment = choose_synthetic_variant(registry, args.canary_run_key, percentage)
        args.prompt_variant = canary_assignment["prompt_variant"]
    if args.mode == "model":
        if not args.permit_external_model or not args.model:
            parser.error("Model evaluation requires --model and --permit-external-model")
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("OPENAI_API_KEY must be configured for explicit model evaluation")
        from openai import OpenAI

        from server.agent.decision_card_model_planner import DecisionCardModelPlanner, prompt_for_variant

        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_API_BASE") or None,
            timeout=20.0,
            max_retries=0,
        )

        frozen_prompt = prompt_for_variant(args.prompt_variant)

        def planner_factory(profile: DecisionCardProfile) -> DecisionCardModelPlanner:
            return DecisionCardModelPlanner(client, args.model, profile, system_prompt=frozen_prompt)

        controller = "REAL_MODEL_NOT_SCRIPTED"
    else:
        if args.permit_external_model or args.model or args.prompt_variant != "baseline":
            parser.error("Baseline mode must not specify paid-model options or experimental prompts")
        planner_factory = None
        controller = "DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM"
    cases = frozen_cases()[: args.max_cases]
    with tempfile.TemporaryDirectory(prefix="gaobao-business-eval-") as temp:
        path = Path(temp) / "synthetic.sqlite"
        os.environ["DATABASE_URL"] = f"sqlite:///{path}"
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=path, reset=True)
        report = run_cases(cases, planner_factory)
        from db.database import engine

        engine.dispose()
    report["controller"] = controller
    report["model_id"] = args.model if controller == "REAL_MODEL_NOT_SCRIPTED" else None
    report["prompt_variant"] = args.prompt_variant if controller == "REAL_MODEL_NOT_SCRIPTED" else None
    # Explicit provenance, not proof of provider execution or human review.
    # Never persist API keys, endpoint URLs, raw model text or student data.
    report["run_id"] = "synthetic_eval_" + secrets.token_hex(12)
    report["evaluation_contract"] = "SYNTHETIC_DECISION_CARD_V1"
    report["temperature"] = 0 if controller == "REAL_MODEL_NOT_SCRIPTED" else None
    report["model_prompt_sha256"] = None
    if controller == "REAL_MODEL_NOT_SCRIPTED":
        report["model_prompt_sha256"] = hashlib.sha256(frozen_prompt.encode("utf-8")).hexdigest()
    report["tool_contract_sha256"] = _digest(
        {
            "tools": ["major_lookup", "admission_lookup", "plan_lookup"],
            "max_tool_calls": 5,
            "require_provider_usage": controller == "REAL_MODEL_NOT_SCRIPTED",
        }
    )
    report["provider_token_source"] = (
        "OPENAI_COMPATIBLE_REPORTED_USAGE" if controller == "REAL_MODEL_NOT_SCRIPTED" else "NOT_MEASURED"
    )
    report["provider_cost_usd"] = None
    report["synthetic_canary_assignment"] = canary_assignment
    report["comparison_eligible"] = (
        canary_assignment is None
        and controller == "REAL_MODEL_NOT_SCRIPTED"
        and report["cases"] == 8
        and report["holdout_cases"] == 4
        and report["trace_validated_cases"] == 8
        and report["provider_prompt_tokens"] is not None
        and report["provider_completion_tokens"] is not None
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if canary_assignment is not None:
        # Preserve the reviewed journal and earlier run artifacts, including
        # aliases or output paths created after the preflight check.
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    else:
        args.out.write_text(serialized, encoding="utf-8")
    print(f"{controller}: verified_business_tasks={report['passed']}/{report['cases']}")
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
