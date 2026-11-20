"""Bounded crash-safe synthetic Deep Agents SQL tool/receipt ledger.

Independent of the LangGraph checkpoint and business TaskRun journal. Same
private checkpoint SQLite FILE, separate short-lived connections. Records the
FIVE approved read-only observations and ONE final validated decision. On
restart all stored observations are checked against current SQL evidence.
This does not provide exactly-once model API calls or real admissions GT.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.agent.deepagents_checkpoint import _profile_digest
from server.agent.synthetic_decision_card import DecisionCardProfile


class UnsafeToolReplay(RuntimeError):
    """Stale, altered, over-budget, or unverified synthetic tool receipt."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class SyntheticDeepAgentsToolLedger:
    """Ledger is usable ONLY after the bound SQLite saver has been opened."""

    def __init__(
        self, path: Path, run_id: str, profile: DecisionCardProfile, model_id: str, contract_sha256: str
    ) -> None:
        if (
            os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true"
            or os.getenv("ENABLE_OUTBOUND_FETCH", "").lower() != "false"
            or os.getenv("APP_ENV", "development").lower() == "production"
        ):
            raise UnsafeToolReplay("Synthetic ledger disabled")
        self.path = Path(path)
        self.run_id = run_id
        self.expected_contract = _profile_digest(profile, model_id, contract_sha256)
        self.last_abstain_reason: str | None = None
        self.allowed = {
            ("major_lookup", profile.major),
            ("admission_lookup", profile.school_a),
            ("admission_lookup", profile.school_b),
            ("plan_lookup", profile.school_a),
            ("plan_lookup", profile.school_b),
        }
        if len(self.allowed) != 5:
            raise UnsafeToolReplay("Exactly five approved synthetic tools required")
        if not self.path.is_absolute() or self.path.is_symlink() or not self.path.is_file():
            raise UnsafeToolReplay("Ledger must reuse private existing checkpoint DB")
        with self._open() as conn:
            binding = conn.execute(
                "SELECT profile_contract_sha256 FROM synthetic_deepagents_bindings WHERE task_id=?",
                (self.run_id,),
            ).fetchone()
            if binding is None or binding[0] != self.expected_contract:
                raise UnsafeToolReplay("Graph checkpoint does not belong to this approved task")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS synthetic_deepagents_tool_ledger (
                    task_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    PRIMARY KEY (task_id, event_index),
                    UNIQUE (task_id, event_kind, event_name, subject)
                )
                """
            )

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _digest(self, ordinal: int, kind: str, name: str, subject: str, payload: dict[str, Any]) -> str:
        value = {
            "task_id": self.run_id,
            "contract": self.expected_contract,
            "index": ordinal,
            "kind": kind,
            "name": name,
            "subject": subject,
            "payload": payload,
        }
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

    def _rows(self) -> list[tuple[str, str, str, dict[str, Any]]]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT event_index,event_kind,event_name,subject,payload_json,event_sha256 "
                "FROM synthetic_deepagents_tool_ledger WHERE task_id=? ORDER BY event_index",
                (self.run_id,),
            ).fetchall()
        if len(rows) > 6:
            raise UnsafeToolReplay("Exceeded bounded tool/receipt budget")
        result = []
        for i, (ordinal, kind, name, subject, raw, digest) in enumerate(rows):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise UnsafeToolReplay("Corrupt observation encoding") from exc
            if ordinal != i or not isinstance(payload, dict) or self._digest(i, kind, name, subject, payload) != digest:
                raise UnsafeToolReplay("Out-of-order or tampered durable observation")
            result.append((kind, name, subject, payload))
        return result

    def load_verified(
        self,
        sql_reader: Callable[[str, str], dict[str, Any]],
        negative: Callable[[str, dict[str, Any]], bool],
    ) -> tuple[list[tuple[str, str, dict[str, Any]]], str | None]:
        """Restore only after checking EACH persisted result against fresh SQL."""
        rows = self._rows()
        self.last_abstain_reason = None
        observations: list[tuple[str, str, dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        receipt: str | None = None
        for i, (kind, name, subject, payload) in enumerate(rows):
            if kind == "tool":
                key = (name, subject)
                if key not in self.allowed or key in seen or receipt is not None or len(observations) >= 5:
                    raise UnsafeToolReplay("Unexpected or repeated persisted SQL observation")
                try:
                    current = sql_reader(name, subject)
                except Exception as exc:
                    raise UnsafeToolReplay("Failed to recheck historical evidence") from exc
                if payload != current or current.get("data_profile") != "SYNTHETIC_DEMO":
                    raise UnsafeToolReplay("Frozen SQL evidence no longer matches source snapshot")
                observations.append((name, subject, payload))
                seen.add(key)
            elif kind == "decision":
                if i != len(rows) - 1 or receipt is not None or name != "final" or subject != "-":
                    raise UnsafeToolReplay("Invalid final receipt ordering")
                sources = [source for _, _, row in observations for source in row.get("source_ids", [])]
                if payload == {"status": "complete", "source_ids": sorted(sources)}:
                    if (
                        seen != self.allowed
                        or len(sources) != 5
                        or len(set(sources)) != 5
                        or any(negative(tool, row) for tool, _, row in observations)
                    ):
                        raise UnsafeToolReplay("Claimed successful tool receipt is not verifiable")
                    receipt = "complete"
                elif set(payload) == {"status", "reason_code"} and payload["status"] == "abstained":
                    if payload["reason_code"] not in {"missing_record", "subject_conflict", "invalid_evidence"}:
                        raise UnsafeToolReplay("Unknown persisted abstention reason")
                    if not any(negative(tool, row) for tool, row in observations):
                        raise UnsafeToolReplay("Abstention lacks observed negative SQL evidence")
                    receipt = "abstained"
                    self.last_abstain_reason = payload["reason_code"]
                else:
                    raise UnsafeToolReplay("Malformed or unauthorised persisted decision")
            else:
                raise UnsafeToolReplay("Unknown synthetic tool event kind")
        return observations, receipt

    def _append(self, kind: str, name: str, subject: str, payload: dict[str, Any]) -> None:
        if len(_canonical(payload)) > 4096:
            raise UnsafeToolReplay("Oversized tool observation")
        with self._open() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT event_kind,event_name,subject FROM synthetic_deepagents_tool_ledger "
                    "WHERE task_id=? ORDER BY event_index",
                    (self.run_id,),
                ).fetchall()
                if len(rows) >= 6 or any(row[0] == "decision" for row in rows):
                    raise UnsafeToolReplay("Ledger is closed or exceeds six event budget")
                if any(row[0] == kind and row[1] == name and row[2] == subject for row in rows):
                    raise UnsafeToolReplay("Repeated tool receipt attempted")
                if kind == "tool" and (name, subject) not in self.allowed:
                    raise UnsafeToolReplay("Unapproved tool receipt attempted")
                if kind == "decision" and (name, subject) != ("final", "-"):
                    raise UnsafeToolReplay("Invalid decision event")
                index = len(rows)
                conn.execute(
                    "INSERT INTO synthetic_deepagents_tool_ledger "
                    "(task_id,event_index,event_kind,event_name,subject,payload_json,event_sha256) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        self.run_id,
                        index,
                        kind,
                        name,
                        subject,
                        _canonical(payload),
                        self._digest(index, kind, name, subject, payload),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def append_tool(self, name: str, subject: str, observation: dict[str, Any]) -> None:
        if (
            (name, subject) not in self.allowed
            or not isinstance(observation, dict)
            or observation.get("data_profile") != "SYNTHETIC_DEMO"
        ):
            raise UnsafeToolReplay("Tool did not return an approved synthetic row")
        self._append("tool", name, subject, observation)

    def append_decision(self, status: str, *, sources: list[str] | None = None, reason: str | None = None) -> None:
        if status == "complete" and sources is not None and reason is None:
            payload = {"status": "complete", "source_ids": sorted(sources)}
        elif status == "abstained" and sources is None and reason is not None:
            payload = {"status": "abstained", "reason_code": reason}
        else:
            raise UnsafeToolReplay("Invalid synthetic decision payload")
        self._append("decision", "final", "-", payload)
