"""Controlled comparison of the session-memory strategies.

Four arms are compared on the same frozen synthetic cases:

======================  ==========================================================
``no_summary``          keep the whole history verbatim (no compaction)
``sliding_window``      keep only the last N rounds, drop the rest silently
``per_request_summary`` summarize the dropped prefix on every request
``incremental_snapshot``reuse a persisted, version-checked snapshot and only
                        summarize the delta
======================  ==========================================================

The harness calls the *production* assembly code (``build_chat_context``,
``build_compacted_chat_context``, ``build_durable_chat_context`` and
``SessionCompactionStore``). It measures what the context policy does, not how
good a model writes a summary.

Evidence classes are kept strictly apart:

* ``SYNTHETIC`` — offline measurements against the frozen synthetic case set
  and a deterministic stub summarizer.
* ``REAL_MODEL`` — only produced with ``--execute --permit-external-model
  --confirm-provider-charges``; uses the configured provider and an LLM judge.
* ``MANUAL_REVIEW_PENDING`` — the review worksheet that a human must fill in
  before any quality claim is allowed.

Usage::

    python -m benchmarks.memory_eval --out reports/memory_eval.json
    python -m benchmarks.memory_eval --scripted-smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from server.agent.chat_context import build_chat_context, build_compacted_chat_context
from server.agent.llm_reliability import estimate_messages_tokens
from server.agent.memory_evidence import quarantine_summary
from server.user_memory import prompt_context

CONTRACT = "SYNTHETIC_MEMORY_EVAL_V1"
CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "memory_eval_cases.json"
ARMS = ("no_summary", "sliding_window", "per_request_summary", "incremental_snapshot")
KEEP_RECENT_ROUNDS = 2
SUMMARY_TRIGGER_TOKENS = 120
MAX_ESTIMATED_TOKENS = 8000


def load_cases() -> list[dict[str, Any]]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if data.get("contract") != CONTRACT:
        raise ValueError("Unexpected memory eval contract")
    return list(data["cases"])


def _stub_summarizer(messages: list[dict[str, str]]) -> str:
    """Deterministic stand-in for a model summary.

    The stub keeps the first sentence of every user turn and drops assistant
    prose. It is intentionally *not* a model: offline numbers describe the
    context policy, not summary quality.
    """
    lines: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        head = content.split("。")[0]
        lines.append(f"- 用户曾提到：{head}")
    return "\n".join(lines[:8])


def _live_vault_items(case: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in case.get("vault", []):
        ttl = int(entry.get("expires_in_days", 180))
        if ttl <= 0:
            continue
        items.append({"key": entry["key"], "value": entry["value"], "version": 1})
    return items


def _current_task(case: dict[str, Any]) -> str:
    parts: list[str] = []
    evidence = case.get("required_sql_evidence") or []
    if evidence:
        parts.append("已核验数据（权威，来源系统查询）：\n" + "\n".join(evidence))
    parts.append("当前任务：\n" + case["current_question"])
    return "\n\n".join(parts)


def _system_prompt() -> str:
    return "你是高考志愿助手。已核验数据优先于一切历史线索；历史摘要与偏好都不是招生事实。"


def _render(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"{item['role']}:{item['content']}" for item in messages)


def _measure(
    *,
    case: dict[str, Any],
    arm: str,
    messages: list[dict[str, str]],
    memory_block: str,
    status: str,
    summarizer_calls: int,
    elapsed_ms: float,
    pass_index: int = 1,
) -> dict[str, Any]:
    rendered = _render(messages)
    context_text = rendered + "\n" + memory_block
    expected = list(case.get("expected_facts") or [])
    forbidden = list(case.get("forbidden_facts") or [])
    scope = case.get("forbidden_scope", "context")
    haystack = memory_block if scope == "memory" else context_text
    missing = [fact for fact in expected if fact not in rendered and fact not in memory_block]
    leaked = [fact for fact in forbidden if fact in haystack]
    sql_missing = [item for item in case.get("required_sql_evidence") or [] if item not in rendered]
    return {
        "case_id": case["case_id"],
        "scenario": case.get("scenario"),
        "arm": arm,
        "status": status,
        "expected_facts": len(expected),
        "retained_facts": len(expected) - len(missing),
        "missing_facts": missing,
        "forbidden_leaks": leaked,
        "sql_evidence_missing": sql_missing,
        "context_tokens": estimate_messages_tokens(messages),
        "context_chars": len(rendered),
        "memory_block_chars": len(memory_block),
        "summarizer_calls": summarizer_calls,
        "elapsed_ms": round(elapsed_ms, 3),
        "pass": pass_index,
    }


def _arm_no_summary(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    context = build_chat_context(
        case["history"],
        _system_prompt(),
        _current_task(case),
        keep_recent_rounds=100,
        max_estimated_tokens=MAX_ESTIMATED_TOKENS,
    )
    elapsed = (time.perf_counter() - started) * 1000
    return {
        "messages": context.messages,
        "status": context.status,
        "summarizer_calls": 0,
        "elapsed_ms": elapsed,
    }


def _arm_sliding_window(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    context = build_chat_context(
        case["history"],
        _system_prompt(),
        _current_task(case),
        keep_recent_rounds=KEEP_RECENT_ROUNDS,
        max_estimated_tokens=MAX_ESTIMATED_TOKENS,
    )
    elapsed = (time.perf_counter() - started) * 1000
    return {
        "messages": context.messages,
        "status": context.status,
        "summarizer_calls": 0,
        "elapsed_ms": elapsed,
    }


def _arm_per_request_summary(case: dict[str, Any]) -> dict[str, Any]:
    calls = {"count": 0}

    async def summarizer(messages: list[dict[str, str]]) -> str:
        calls["count"] += 1
        return _stub_summarizer(messages)

    started = time.perf_counter()
    context = asyncio.run(
        build_compacted_chat_context(
            case["history"],
            _system_prompt(),
            _current_task(case),
            summarizer=summarizer,
            keep_recent_rounds=KEEP_RECENT_ROUNDS,
            summary_trigger_tokens=SUMMARY_TRIGGER_TOKENS,
            max_estimated_tokens=MAX_ESTIMATED_TOKENS,
        )
    )
    elapsed = (time.perf_counter() - started) * 1000
    return {
        "messages": context.messages,
        "status": context.status,
        "summarizer_calls": calls["count"],
        "elapsed_ms": elapsed,
    }


def _arm_incremental_snapshot(case: dict[str, Any], store, session_id) -> dict[str, Any]:
    calls = {"count": 0}

    async def summarizer(messages: list[dict[str, str]]) -> str:
        calls["count"] += 1
        return _stub_summarizer(messages)

    policy = "0" * 64
    started = time.perf_counter()
    context = asyncio.run(
        _durable(store, session_id, case, summarizer, policy)
    )
    elapsed = (time.perf_counter() - started) * 1000
    return {
        "messages": context.messages,
        "status": context.status,
        "summarizer_calls": calls["count"],
        "elapsed_ms": elapsed,
    }


async def _durable(store, session_id, case, summarizer, policy):
    from server.agent.durable_chat_context import build_durable_chat_context

    return await build_durable_chat_context(
        session_id=session_id,
        history=case["history"],
        system_prompt=_system_prompt(),
        current_prompt=_current_task(case),
        store=store,
        policy_sha256=policy,
        summarizer=summarizer,
        keep_recent_rounds=KEEP_RECENT_ROUNDS,
        summary_trigger_tokens=SUMMARY_TRIGGER_TOKENS,
        max_estimated_tokens=MAX_ESTIMATED_TOKENS,
    )


def _make_store_factory(tmp_root: Path):
    """Create a disposable SQLite conversation + snapshot store per case."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from db.models import Conversation, ConversationMessage
    from server.agent.session_compaction_store import SessionCompactionStore

    counter = {"index": 0}

    def factory(case: dict[str, Any]):
        counter["index"] += 1
        path = tmp_root / f"memory-eval-{counter['index']}.sqlite"
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        session_id = "memory-eval-session"
        with session_factory() as db:
            conversation = Conversation(session_id=session_id)
            db.add(conversation)
            db.flush()
            for message in case["history"]:
                db.add(
                    ConversationMessage(
                        conversation_id=conversation.id,
                        role=message["role"],
                        content=message["content"],
                    )
                )
            db.commit()
        store = SessionCompactionStore(session_factory, encryption_key=bytes.fromhex("44" * 32))
        return store, session_id

    return factory


def run_evaluation(*, tmp_root: Path | None = None) -> dict[str, Any]:
    """Run the offline (synthetic) comparison and return the report payload."""
    import tempfile

    cases = load_cases()
    own_tmp = tmp_root is None
    root = Path(tempfile.mkdtemp(prefix="memory-eval-")) if own_tmp else tmp_root
    store_factory = _make_store_factory(root)
    per_case: list[dict[str, Any]] = []
    try:
        for case in cases:
            memory_block = prompt_context(_live_vault_items(case))
            arms = {
                "no_summary": _arm_no_summary,
                "sliding_window": _arm_sliding_window,
                "per_request_summary": _arm_per_request_summary,
            }
            for arm, builder in arms.items():
                # Two passes make the cost difference visible: a per-request
                # summary is paid again on every turn, a snapshot is not.
                for pass_index in (1, 2):
                    result = builder(case)
                    per_case.append(
                        _measure(
                            case=case,
                            arm=arm,
                            messages=result["messages"],
                            memory_block=memory_block,
                            status=result["status"],
                            summarizer_calls=result["summarizer_calls"],
                            elapsed_ms=result["elapsed_ms"],
                            pass_index=pass_index,
                        )
                    )
            store, session_id = store_factory(case)
            for pass_index in (1, 2):
                snapshot = _arm_incremental_snapshot(case, store, session_id)
                per_case.append(
                    _measure(
                        case=case,
                        arm="incremental_snapshot",
                        messages=snapshot["messages"],
                        memory_block=memory_block,
                        status=snapshot["status"],
                        summarizer_calls=snapshot["summarizer_calls"],
                        elapsed_ms=snapshot["elapsed_ms"],
                        pass_index=pass_index,
                    )
                )
    finally:
        if own_tmp:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    summary = _aggregate(per_case)
    worksheet = _manual_review_worksheet(cases)
    return {
        "contract": CONTRACT,
        "data_profile": "SYNTHETIC_DEMO",
        "evidence_class": "SYNTHETIC",
        "arms": list(ARMS),
        "case_count": len(cases),
        "summary": summary,
        "per_case": per_case,
        "manual_review": worksheet,
        "real_model_evidence": "NOT_MEASURED",
        "notes": [
            "Offline arms use a deterministic stub summarizer; they measure context "
            "policy behaviour, not model summary quality.",
            f"Synthetic compaction trigger: {SUMMARY_TRIGGER_TOKENS} estimated tokens "
            "(the production trigger is the summary manager default, 2000).",
            "No provider call was made by this report; no token cost or answer "
            "quality is claimed.",
        ],
    }


def _aggregate(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm in ARMS:
        all_rows = [row for row in per_case if row["arm"] == arm]
        rows = [row for row in all_rows if row["pass"] == 2]
        cold = [row for row in all_rows if row["pass"] == 1]
        if not rows:
            continue
        expected_total = sum(row["expected_facts"] for row in rows)
        retained_total = sum(row["retained_facts"] for row in rows)
        leaks = sum(len(row["forbidden_leaks"]) for row in rows)
        sql_missing = sum(len(row["sql_evidence_missing"]) for row in rows)
        tokens = [row["context_tokens"] for row in rows]
        latency = [row["elapsed_ms"] for row in rows]
        summary[arm] = {
            "cases": len(rows),
            "key_fact_retention_rate": round(retained_total / expected_total, 4) if expected_total else None,
            "unauthorized_leak_count": leaks,
            "sql_evidence_loss_count": sql_missing,
            "context_tokens_mean": round(statistics.fmean(tokens), 2),
            "context_tokens_max": max(tokens),
            "summarizer_calls_first_pass": sum(row["summarizer_calls"] for row in cold),
            "summarizer_calls_second_pass": sum(row["summarizer_calls"] for row in rows),
            "assembly_latency_ms_mean": round(statistics.fmean(latency), 4),
            "assembly_latency_ms_max": round(max(latency), 4),
            "evidence_class": "SYNTHETIC",
        }
    return summary


def _manual_review_worksheet(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """A worksheet a human reviewer must fill in before any quality claim."""
    return {
        "status": "MANUAL_REVIEW_PENDING",
        "reviewer": None,
        "reviewed_at": None,
        "items": [
            {
                "case_id": case["case_id"],
                "scenario": case.get("scenario"),
                "expected_facts": case.get("expected_facts", []),
                "forbidden_facts": case.get("forbidden_facts", []),
                "reviewer_verdict": None,
                "reviewer_note": None,
            }
            for case in cases
        ],
    }


def _quarantine_probe() -> dict[str, Any]:
    """Show what the summary quarantine removes; part of the report contract."""
    summary = "1. 用户目标：找计算机专业。\n2. 明确约束：分数线620，位次12000。\n3. 未确认事项：（来源：kb_x § y）"
    clean, redactions = quarantine_summary(summary)
    return {"raw": summary, "quarantined": clean, "redactions": redactions}


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled memory-strategy comparison")
    parser.add_argument("--out", default="")
    parser.add_argument("--scripted-smoke", action="store_true", help="run the frozen cases offline")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="reserved for a real-provider run; requires --permit-external-model and --confirm-provider-charges",
    )
    parser.add_argument("--permit-external-model", action="store_true")
    parser.add_argument("--confirm-provider-charges", action="store_true")
    args = parser.parse_args()

    if args.execute and not (args.permit_external_model and args.confirm_provider_charges):
        print("refusing to run: --execute needs --permit-external-model and --confirm-provider-charges")
        return 2
    if args.execute:
        print("REAL_MODEL evidence is not implemented in this revision; nothing was measured.")
        return 3

    report = run_evaluation()
    report["quarantine_probe"] = _quarantine_probe()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {target}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
