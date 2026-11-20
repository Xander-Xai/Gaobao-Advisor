"""Local optional LangGraph/Deep Agents durable checkpoint foundation.

Only for approved synthetic TaskRuns. SQLite persistence is cross-process, but
the admission tool-observation ledger and Deep Agents final receipt are STILL
process-local. Existing graph snapshots intentionally FAIL CLOSED on retry:
do not claim seamless Deep Agents task resume or exactly-once execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.agent.synthetic_decision_card import DecisionCardProfile

_TASK_ID_RE = re.compile(r"^syn_[0-9a-f]{32}$")
_HEX_SHA = re.compile(r"^[0-9a-f]{64}$")


def _profile_digest(profile: DecisionCardProfile, model_id: str, contract_sha256: str) -> str:
    if not isinstance(profile, DecisionCardProfile):
        raise ValueError("Only an approved fictional DecisionCardProfile can be checkpointed")
    if not isinstance(model_id, str) or not re.fullmatch(r"openai:[A-Za-z0-9_.-]{2,100}", model_id):
        raise ValueError("Invalid fixed model identifier")
    if not isinstance(contract_sha256, str) or not _HEX_SHA.fullmatch(contract_sha256):
        raise ValueError("Missing frozen model and tool contract SHA")
    from scripts.seed_demo_data import DATASET_VERSION, DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR

    if (
        profile.school_a not in {row[0] for row in DEMO_SCHOOLS}
        or profile.school_b not in {row[0] for row in DEMO_SCHOOLS}
        or profile.school_a == profile.school_b
        or profile.major not in {row[0] for row in DEMO_MAJORS}
        or profile.province not in {"示例省·甲", "示例省·乙", "示例省·丙", "示例省·丁"}
        or type(profile.score) is not int
        or not 100 <= profile.score <= 750
        or profile.subject_type not in {"物理类", "历史类"}
        or type(profile.year) is not int
        or profile.year != DEMO_YEAR
    ):
        raise ValueError("Only frozen fictional TaskRun profiles can be checkpointed")

    payload: dict[str, Any] = {
        "data_profile": "SYNTHETIC_DEMO",
        "profile": asdict(profile),
        "model_id": model_id,
        "contract_sha256": contract_sha256,
        "dataset_version": DATASET_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@contextmanager
def open_synthetic_deepagents_checkpointer(
    path: Path, task_id: str, profile: DecisionCardProfile, model_id: str, contract_sha256: str
) -> Iterator[tuple[Any, bool]]:
    """Yield (actual SqliteSaver, has_prior_graph_state) with strict binding.

    A prior snapshot is offered ONLY for inspection: the calling Deep Agents
    adapter explicitly refuses to blindly resume because tool receipts are
    still held in local Python objects.
    """
    if (
        os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
        or os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false"
        or os.getenv("APP_ENV", "development").lower() == "production"
    ):
        raise ValueError("Durable checkpoint experiment is disabled or unsafe")
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("Checkpoint requires an approved synthetic TaskRun identifier")
    digest = _profile_digest(profile, model_id, contract_sha256)
    original = Path(path)
    if not original.is_absolute() or original.is_symlink() or not original.parent.is_dir():
        raise ValueError("Checkpoint path must be absolute, non-symlink and in an existing directory")
    resolved = original.resolve()
    journal_path = os.getenv("SYNTHETIC_TASKRUN_JOURNAL", "")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Checkpoint experiment requires fictional SQLite admissions data")
    forbidden = {Path(database_url.removeprefix("sqlite:///")).resolve()}
    if journal_path:
        forbidden.add(Path(journal_path).resolve())
    if resolved in forbidden:
        raise ValueError("Checkpoint file must be distinct from admissions data and the TaskRun journal")
    try:
        fd = os.open(resolved, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    mode = resolved.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError("Checkpoint file must be private (0600) and regular")

    # This import is intentionally optional: default production app must not
    # depend on or load Deep Agents / LangGraph checkpoint-sqlite.
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(resolved, isolation_level=None, timeout=5.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS synthetic_deepagents_bindings "
            "(task_id TEXT PRIMARY KEY, profile_contract_sha256 TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        binding = conn.execute(
            "SELECT profile_contract_sha256 FROM synthetic_deepagents_bindings WHERE task_id=?", (task_id,)
        ).fetchone()
        if binding is not None and binding[0] != digest:
            raise ValueError("Checkpoint task identity is bound to a different model/profile/contract")
        if binding is None:
            conn.execute(
                "INSERT INTO synthetic_deepagents_bindings (task_id,profile_contract_sha256,created_at) "
                "VALUES (?,?,?)",
                (task_id, digest, time.time()),
            )
        conn.commit()
        saver = SqliteSaver(conn)
        config = {"configurable": {"thread_id": task_id, "checkpoint_ns": ""}}
        previous = saver.get_tuple(config) is not None
        yield saver, previous
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
