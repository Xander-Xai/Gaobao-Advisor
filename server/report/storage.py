"""File-based report storage — saves/loads Report as JSON."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from server.report.models import Report

_DEFAULT_BASE = Path(__file__).resolve().parent.parent.parent / "data" / "reports"

# Session and report identifiers become path components. Restrict them to a
# conservative charset so a crafted id can never escape the storage root
# (``../``, ``/``, NUL, or absolute paths all fail this check).
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _safe_component(value: str, label: str) -> str:
    """Return a basename-verified safe component, else raise.

    os.path.basename is an explicit path-traversal sanitizer; the equality
    check rejects any input that would have been changed by normalization.
    """
    if not isinstance(value, str):
        raise ValueError(f"unsafe {label}: must match [A-Za-z0-9_-]{{1,128}}")
    component = os.path.basename(value)
    if component != value or not _SAFE_COMPONENT_RE.fullmatch(component):
        raise ValueError(f"unsafe {label}: must match [A-Za-z0-9_-]{{1,128}}")
    return component


class ReportStorage:
    """File-based storage for Report objects.

    Reports are stored as JSON files at: base_dir/{session_id}/{report_id}.json
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _DEFAULT_BASE

    def _session_directory(self, session_id: str) -> Path:
        """Validate a direct report-root child; reject symlinks before access."""
        component = _safe_component(session_id, "session_id")
        candidate = self._base / component
        if (
            self._base.is_symlink()
            or candidate.is_symlink()
            or not candidate.resolve(strict=False).is_relative_to(self._base.resolve(strict=False))
        ):
            raise ValueError("Unsafe report session directory")
        return candidate

    def _report_path(self, session_id: str, report_id: str) -> Path:
        filename = _safe_component(report_id, "report_id")
        candidate = self._session_directory(session_id) / f"{filename}.json"
        if candidate.is_symlink() or not candidate.resolve(strict=False).is_relative_to(
            self._base.resolve(strict=False)
        ):
            raise ValueError("Unsafe report file path")
        return candidate

    def save(self, report: Report) -> Path:
        """Save a Report to disk. Returns the file path written.

        Raises:
            ValueError: if ``session_id`` or ``report.id`` is not a safe path
                component (defence against path traversal / storage pollution).
        """
        dest = self._report_path(report.session_id, report.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return dest

    def load(self, report_id: str) -> Report | None:
        """Search all session dirs for a report by id. Returns None if not found.

        Raises:
            ValueError: if ``report_id`` is not a safe path component.
        """
        report_file = _safe_component(report_id, "report_id")
        if self._base.is_symlink():
            raise ValueError("Unsafe report storage root")
        if not self._base.exists():
            return None
        for session_dir in sorted(self._base.iterdir()):
            if (
                session_dir.is_symlink()
                or not _SAFE_COMPONENT_RE.fullmatch(session_dir.name)
                or not session_dir.is_dir()
            ):
                continue
            candidate = self._report_path(session_dir.name, report_file)
            if candidate.exists():
                return self._load_file(candidate)
        return None

    def load_by_session(self, report_id: str, session_id: str) -> Report | None:
        """Direct load by session_id — no search needed."""
        path = self._report_path(session_id, report_id)
        if path.exists():
            return self._load_file(path)
        return None

    def list_by_session(self, session_id: str) -> list[Report]:
        """Return all reports for a given session, sorted by creation time."""
        session_dir = self._session_directory(session_id)
        if not session_dir.exists():
            return []
        reports: list[Report] = []
        for fp in sorted(session_dir.glob("*.json")):
            if fp.is_symlink() or not fp.is_file() or not _SAFE_COMPONENT_RE.fullmatch(fp.stem):
                raise ValueError("Unsafe report entry in session directory")
            reports.append(self._load_file(fp))
        return reports

    def stage_session_deletion(self, session_id: str) -> tuple[Path, int] | None:
        """Atomically hide validated reports before the database transaction.

        Reports in .purge-staging are not visible to load(). No arbitrary path
        or symlink may be deleted. This is not a cross-process writer lock.
        """
        session_dir = self._session_directory(session_id)
        if session_dir.is_symlink():
            raise ValueError("Symlinked report directory is unsafe")
        if not session_dir.exists():
            return None
        if not session_dir.is_dir():
            raise ValueError("Report storage entry is not a directory")
        entries = list(session_dir.iterdir())
        if len(entries) > 1000 or any(
            item.is_symlink()
            or not item.is_file()
            or item.suffix != ".json"
            or not _SAFE_COMPONENT_RE.fullmatch(item.stem)
            for item in entries
        ):
            raise ValueError("Report directory contains unrecognized files")
        staged_root = self._base / ".purge-staging"
        if staged_root.is_symlink():
            raise ValueError("Report staging root cannot be a symlink")
        staged_root.mkdir(parents=True, exist_ok=True)
        if staged_root.is_symlink():
            raise ValueError("Report staging root cannot be a symlink")
        staged = staged_root / ("pending-" + uuid.uuid4().hex)
        os.replace(session_dir, staged)
        return staged, len(entries)

    def restore_staged_session(self, session_id: str, staged: Path) -> None:
        """Restore hidden reports only when the SQL transaction fails."""
        original = self._session_directory(session_id)
        self._validate_stage(staged)
        if original.exists() or original.is_symlink():
            raise RuntimeError("Original report directory was recreated during erasure")
        os.replace(staged, original)

    def finalize_staged_session(self, staged: Path) -> int:
        """Delete staged JSON files; never follow child symlinks or arbitrary paths."""
        self._validate_stage(staged)
        entries = list(staged.iterdir())
        if len(entries) > 1000 or any(
            item.is_symlink()
            or not item.is_file()
            or item.suffix != ".json"
            or not _SAFE_COMPONENT_RE.fullmatch(item.stem)
            for item in entries
        ):
            raise ValueError("Staged report directory changed unexpectedly")
        for item in entries:
            item.unlink()
        staged.rmdir()
        return len(entries)

    def _validate_stage(self, staged: Path) -> None:
        root = self._base / ".purge-staging"
        if (
            staged.is_symlink()
            or root.is_symlink()
            or staged.parent.resolve() != root.resolve()
            or not re.fullmatch(r"pending-[a-f0-9]{32}", staged.name)
            or not staged.is_dir()
        ):
            raise ValueError("Invalid isolated report deletion stage")

    @staticmethod
    def _load_file(path: Path) -> Report:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Report.from_dict(data)
