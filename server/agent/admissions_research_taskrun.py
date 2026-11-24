"""Private, approved admissions research journal and one-shot execution worker.

No HTTP request starts a model or Docker. Approved model, dataset, scope and
image are immutable. A lost lease never auto-replays a paid task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from server.agent.admissions_analysis_sandbox import _expected
from server.agent.admissions_deepagents import MODEL_RE, ResearchScope, _coverage, run_admissions_research

ID = re.compile(r"research_[0-9a-f]{32}")
KEY = re.compile(r"[A-Za-z0-9_.:-]{8,96}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
DATASET = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
TERMINAL = {"SUCCEEDED", "NEEDS_REVIEW", "FAILED", "CANCELLED", "EXPIRED"}


class ResearchConflict(RuntimeError):
    pass


class ResearchNotFound(LookupError):
    pass


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def configured_dataset() -> str:
    if os.getenv("ADMISSIONS_RESEARCH_ENABLED", "").lower() != "true":
        raise ValueError("Approved admissions research is disabled")
    ref = os.getenv("ADMISSIONS_RESEARCH_DATASET_REF", "")
    if not DATASET.fullmatch(ref):
        raise ValueError("Explicit authorized dataset reference required")
    return ref


def validate_spec(spec: dict) -> ResearchScope:
    if set(spec) != {"scope", "model_id", "dataset_ref", "image_id", "approval_ref"}:
        raise ValueError("Invalid research spec")
    if (
        not isinstance(spec["model_id"], str)
        or not MODEL_RE.fullmatch(spec["model_id"])
        or not isinstance(spec["dataset_ref"], str)
        or not DATASET.fullmatch(spec["dataset_ref"])
        or not isinstance(spec["image_id"], str)
        or not IMAGE.fullmatch(spec["image_id"])
        or not isinstance(spec["approval_ref"], str)
        or not 1 <= len(spec["approval_ref"].strip()) <= 200
    ):
        raise ValueError("Invalid research authorization")
    scope = ResearchScope(**spec["scope"])
    if len(canonical(spec)) > 2000:
        raise ValueError("Research request too large")
    return scope


class ResearchJournal:
    """Local single-host CAS journal; not the synthetic store or admissions database."""

    def __init__(self, path: Path):
        if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Absolute private journal path required")
        parent = path.parent.stat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077 or parent.st_uid != os.getuid():
            raise ValueError("Journal directory must be owned by worker and mode 0700")
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Private regular journal file with one link required")
        url = os.getenv("DATABASE_URL", "")
        if url.startswith("sqlite:///") and Path(url.removeprefix("sqlite:///")).resolve() == path.resolve():
            raise ValueError("Journal must not be the admissions database")
        synthetic = os.getenv("SYNTHETIC_TASKRUN_JOURNAL")
        if synthetic and Path(synthetic).resolve() == path.resolve():
            raise ValueError("Journal must not be the synthetic task store")
        self.path = path
        with self.tx() as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name IN (?, ?)",
                ("synthetic_task_runs", "synthetic_portfolio_reviews"),
            ).fetchone():
                raise ValueError("Private research journal must not contain synthetic task schema")
            db.execute(
                """CREATE TABLE IF NOT EXISTS authorized_research_runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, idem TEXT NOT NULL,
                spec TEXT NOT NULL, spec_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                approver TEXT, lease TEXT, deadline REAL, result TEXT,
                failure TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(owner, idem))"""
            )

    def _check_private_file(self):
        # A hard-linked alias would otherwise use a different worker-lock pathname.
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Private regular journal file with one link required")

    @contextmanager
    def tx(self):
        self._check_private_file()
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def execution_slot(self):
        """Serialize paid execution on one Linux host, including cancelled calls in flight."""
        import fcntl

        self._check_private_file()
        lock_path = self.path.with_name(self.path.name + ".worker.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise ValueError("Private regular worker lock required")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ResearchConflict("Research worker already active") from None
            # Do not unlink this file: competing processes must lock the same inode.
            # Closing the descriptor releases the lock on normal exit or process death.
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _row(db, run_id):
        if not ID.fullmatch(run_id):
            raise ResearchNotFound("Unknown research run")
        row = db.execute("SELECT * FROM authorized_research_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ResearchNotFound("Unknown research run")
        if row["state"] == "RUNNING" and row["deadline"] <= time.time():
            db.execute(
                "UPDATE authorized_research_runs SET state='EXPIRED',lease=NULL,deadline=NULL,"
                "failure='lease_expired_no_replay',updated=? WHERE id=?",
                (time.time(), run_id),
            )
            row = db.execute("SELECT * FROM authorized_research_runs WHERE id=?", (run_id,)).fetchone()
        return row

    @staticmethod
    def _view(row):
        spec = json.loads(row["spec"])
        spec["approval_ref"] = None
        return {
            "run_id": row["id"],
            "owner": row["owner"],
            "status": row["state"],
            "spec": spec,
            "spec_sha256": row["spec_sha256"],
            "approved_by": row["approver"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "failure_code": row["failure"],
            "created_at": row["created"],
            "updated_at": row["updated"],
            "automatic_paid_replay": False,
        }

    @staticmethod
    def _visible(row, actor, admin):
        if row["owner"] != actor and not admin:
            raise ResearchNotFound("Unknown research run")

    def create(self, owner: str, key: str, spec: dict):
        validate_spec(spec)
        self.expire_abandoned()
        if not owner or not KEY.fullmatch(key):
            raise ValueError("Invalid owner or idempotency key")
        with self.tx() as db:
            existing = db.execute(
                "SELECT * FROM authorized_research_runs WHERE owner=? AND idem=?", (owner, key)
            ).fetchone()
            if existing:
                if existing["spec_sha256"] != digest(spec):
                    raise ResearchConflict("Idempotency conflict")
                return self._view(self._row(db, existing["id"]))
            if (
                db.execute(
                    "SELECT COUNT(*) FROM authorized_research_runs WHERE owner=? "
                    "AND state IN ('PENDING_APPROVAL','APPROVED','RUNNING')",
                    (owner,),
                ).fetchone()[0]
                >= 8
            ):
                raise ResearchConflict("Outstanding task quota")
            if db.execute("SELECT COUNT(*) FROM authorized_research_runs").fetchone()[0] >= 5000:
                raise ResearchConflict("Journal quota")
            run_id = "research_" + secrets.token_hex(16)
            now = time.time()
            db.execute(
                "INSERT INTO authorized_research_runs "
                "(id,owner,idem,spec,spec_sha256,state,created,updated) VALUES(?,?,?,?,?,'PENDING_APPROVAL',?,?)",
                (run_id, owner, key, canonical(spec), digest(spec), now, now),
            )
            return self._view(self._row(db, run_id))

    def get(self, run_id: str, actor: str, *, admin: bool = False):
        with self.tx() as db:
            row = self._row(db, run_id)
            self._visible(row, actor, admin)
            return self._view(row)

    def approve(self, run_id: str, approver: str, spec_sha256: str):
        with self.tx() as db:
            row = self._row(db, run_id)
            if row["owner"] == approver or row["state"] != "PENDING_APPROVAL" or row["spec_sha256"] != spec_sha256:
                raise ResearchConflict("Independent approval of exact request required")
            db.execute(
                "UPDATE authorized_research_runs SET state='APPROVED',approver=?,updated=? WHERE id=?",
                (approver, time.time(), run_id),
            )
            return self._view(self._row(db, run_id))

    def cancel(self, run_id: str, actor: str, *, admin: bool = False):
        with self.tx() as db:
            row = self._row(db, run_id)
            self._visible(row, actor, admin)
            if row["state"] in TERMINAL:
                raise ResearchConflict("Terminal run")
            db.execute(
                "UPDATE authorized_research_runs SET state='CANCELLED',lease=NULL,deadline=NULL,updated=? WHERE id=?",
                (time.time(), run_id),
            )
            return self._view(self._row(db, run_id))

    def expire_abandoned(self) -> int:
        """Commit expiry separately so later guard exceptions cannot roll it back."""
        now = time.time()
        with self.tx() as db:
            changed = db.execute(
                "UPDATE authorized_research_runs SET state='EXPIRED',lease=NULL,deadline=NULL,"
                "failure='lease_expired_no_replay',updated=? WHERE state='RUNNING' AND deadline<=?",
                (now, now),
            )
            return changed.rowcount

    def approved_candidates(self, dataset_ref: str, model_id: str, image_id: str) -> list[str]:
        """Select only immutable, approved requests within an operator's execution policy."""
        if not DATASET.fullmatch(dataset_ref) or not MODEL_RE.fullmatch(model_id) or not IMAGE.fullmatch(image_id):
            raise ValueError("Invalid dispatcher policy")
        self.expire_abandoned()
        with self.tx() as db:
            rows = db.execute(
                "SELECT id,spec,spec_sha256 FROM authorized_research_runs "
                "WHERE state='APPROVED' AND approver IS NOT NULL ORDER BY created,id LIMIT 5000"
            ).fetchall()
            selected = []
            for row in rows:
                try:
                    spec = json.loads(row["spec"])
                    validate_spec(spec)
                    if (
                        digest(spec) == row["spec_sha256"]
                        and spec["dataset_ref"] == dataset_ref
                        and spec["model_id"] == model_id
                        and spec["image_id"] == image_id
                    ):
                        selected.append(row["id"])
                except (TypeError, ValueError):
                    continue
            return selected

    def claim(self, run_id: str, dataset_ref: str):
        with self.tx() as db:
            row = self._row(db, run_id)
            spec = json.loads(row["spec"])
            if (
                row["state"] != "APPROVED"
                or not row["approver"]
                or spec["dataset_ref"] != dataset_ref
                or digest(spec) != row["spec_sha256"]
            ):
                raise ResearchConflict("Approved matching dataset required; paid runs are never replayed")
            lease = secrets.token_hex(16)
            now = time.time()
            db.execute(
                "UPDATE authorized_research_runs SET state='RUNNING',lease=?,deadline=?,updated=? WHERE id=?",
                (lease, now + 600, now, run_id),
            )
            return spec, lease

    def guard(self, run_id: str, lease: str):
        self.expire_abandoned()
        with self.tx() as db:
            row = self._row(db, run_id)
            if row["state"] != "RUNNING" or row["lease"] != lease:
                raise ResearchConflict("Lease lost or task cancelled")

    def finish(self, run_id: str, lease: str, result: dict | None, failure: str | None):
        with self.tx() as db:
            row = self._row(db, run_id)
            if row["state"] == "RUNNING" and row["lease"] == lease:
                state = "FAILED"
                if result is not None:
                    if len(canonical(result)) > 50000:
                        raise ValueError("Result exceeds journal budget")
                    state = "SUCCEEDED" if result["status"] == "VERIFIED_RESEARCH" else "NEEDS_REVIEW"
                db.execute(
                    "UPDATE authorized_research_runs SET state=?,result=?,failure=?,lease=NULL,deadline=NULL,updated=? "
                    "WHERE id=?",
                    (state, canonical(result) if result else None, failure, time.time(), run_id),
                )
                return self._view(self._row(db, run_id))
        # Commit any expiry performed by _row before reporting the conflict.
        # Raising inside tx would roll back the EXPIRED transition.
        raise ResearchConflict("Lease lost or task cancelled")


def open_configured_journal():
    configured_dataset()
    path = os.getenv("ADMISSIONS_RESEARCH_JOURNAL", "")
    if not path:
        raise ValueError("Research journal not configured")
    return ResearchJournal(Path(path))


def execute_approved(journal, run_id, dataset_ref, session_factory, *, runner=run_admissions_research):
    with journal.execution_slot():
        return _execute_approved_under_lock(journal, run_id, dataset_ref, session_factory, runner=runner)


def _execute_approved_under_lock(journal, run_id, dataset_ref, session_factory, *, runner):
    spec, lease = journal.claim(run_id, dataset_ref)

    def guard():
        journal.guard(run_id, lease)

    try:
        scope = validate_spec(spec)
        report = runner(
            scope,
            session_factory,
            spec["model_id"],
            approval_ref=spec["approval_ref"],
            authorized=True,
            model_authorized=True,
            analysis_image_id=spec["image_id"],
            lease_guard=guard,
        )
        guard()
        evidence = report.get("evidence")
        analysis = report.get("sandbox_analysis")
        if (
            report.get("status") not in {"VERIFIED_RESEARCH", "VERIFIED_PARTIAL_RESEARCH"}
            or canonical(report.get("scope")) != canonical(asdict(scope))
            or report.get("approval_ref_sha256") != digest(spec["approval_ref"])
            or report.get("admission_recommendation_verified") is not False
            or not isinstance(evidence, dict)
            or set(evidence) != {"admission-history", "enrollment-plans"}
            or report.get("evidence_sha256") != digest(evidence)
            or not isinstance(analysis, dict)
            or analysis.get("status") != "VERIFIED_DESCRIPTIVE_ANALYSIS"
            or analysis.get("result_sha256") != digest(analysis.get("result"))
            or analysis.get("cleanup_verified") is not True
            or analysis.get("image_id") != spec["image_id"]
            or analysis.get("input_sha256")
            != digest({"rows": evidence["admission-history"], "group_by": ["school_id", "year"]})
        ):
            return journal.finish(run_id, lease, None, "unverified_research")
        coverage = {kind: _coverage(rows, scope, kind) for kind, rows in evidence.items()}
        partial = any(gaps for dimension in coverage.values() for gaps in dimension.values())
        if (
            coverage != report.get("coverage")
            or partial != (report["status"] == "VERIFIED_PARTIAL_RESEARCH")
            or canonical(_expected(evidence["admission-history"], ("school_id", "year")))
            != canonical(analysis["result"])
        ):
            return journal.finish(run_id, lease, None, "unverified_research")
        # Retain only bounded aggregate results, coverage and source hashes.
        result = {
            "status": report["status"],
            "coverage": report["coverage"],
            "evidence_sha256": report["evidence_sha256"],
            "receipt_ids": report["receipt_ids"],
            "analysis": analysis["result"],
            "analysis_sha256": analysis["result_sha256"],
            "provider_usage": report.get("provider_usage"),
            "admission_recommendation_verified": False,
            "spec_sha256": digest(spec),
        }
        return journal.finish(run_id, lease, result, None)
    except ResearchConflict:
        raise
    except Exception:
        return journal.finish(run_id, lease, None, "research_execution_failed")


def render_aggregate_report(item: dict) -> str:
    """Render only the owner-authorized journal projection, never raw SQL or model prose."""
    result = item.get("result")
    if item.get("status") not in {"SUCCEEDED", "NEEDS_REVIEW"} or not isinstance(result, dict):
        raise ResearchConflict("No verified aggregate report")
    if (
        result.get("spec_sha256") != item.get("spec_sha256")
        or result.get("analysis_sha256") != digest(result.get("analysis"))
        or result.get("admission_recommendation_verified") is not False
    ):
        raise ResearchConflict("Report evidence mismatch")
    return "\n".join(
        [
            "高考志愿历史数据研究（内部汇总，不是录取预测）",
            "任务: " + canonical(item["run_id"]),
            "状态: " + item["status"],
            "研究范围: " + canonical(item["spec"]["scope"]),
            "覆盖缺口: " + canonical(result["coverage"]),
            "历史分数与位次统计: " + canonical(result["analysis"]),
            "证据摘要: " + result["evidence_sha256"],
            "招生计划仅为覆盖核验；选科资格与数据来源真实性尚未独立认证。",
            "",
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authorize-model-call", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not os.getenv("DATABASE_URL") or not os.getenv("OPENAI_API_KEY"):
        parser.error("Explicit approved data and model credentials required")
    from db.database import get_session

    result = execute_approved(open_configured_journal(), args.run_id, configured_dataset(), get_session)
    print(canonical({"run_id": args.run_id, "status": result["status"]}))
    return 0 if result["status"] in {"SUCCEEDED", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
