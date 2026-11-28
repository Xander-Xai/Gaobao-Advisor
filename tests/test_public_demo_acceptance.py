"""PUBLIC DEMO acceptance tests.

Covers the three properties this repository must demonstrate to a reviewer who
has never seen the private original:

1. The bundled dataset is synthetic, reproducible and self-labelling.
2. Session identity is server-issued and cannot be minted by a caller
   (the control that upstream fixed in PR #11 and must not regress).
3. Disabled subsystems are reported as disabled, never as success.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from scripts.seed_demo_data import (
    DEMO_PROVINCES,
    DEMO_YEARS,
    SYNTHETIC_MARKER,
    build_payload,
    seed_database,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── 1. Synthetic data ────────────────────────────────────────────────────
class TestSyntheticDataset:
    def test_payload_is_deterministic(self) -> None:
        import json

        first = json.dumps(build_payload(), sort_keys=True, ensure_ascii=False)
        second = json.dumps(build_payload(), sort_keys=True, ensure_ascii=False)
        assert first == second, "synthetic data must be reproducible"

    def test_metadata_declares_synthetic(self) -> None:
        meta = build_payload()["metadata"]
        assert meta["marker"] == SYNTHETIC_MARKER
        assert "合成演示数据" in meta["notice"]

    def test_no_real_institution_names(self) -> None:
        """Every institution carries the （合成） marker."""
        for school in build_payload()["schools"]:
            assert "（合成）" in school["name"], school["name"]

    def test_provinces_are_invented(self) -> None:
        for province in DEMO_PROVINCES:
            assert province.startswith("示例省"), province

    def test_scores_stay_in_plausible_range(self) -> None:
        for row in build_payload()["admission_scores"]:
            assert 300 <= row["min_score"] <= 750
            assert row["min_score"] <= row["max_score"] <= 750

    def test_schools_carry_provenance_in_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "demo.db"
        seed_database(db_path, reset=True)

        conn = sqlite3.connect(db_path)
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM schools")]
            markers = {r[0] for r in conn.execute("SELECT special_type FROM schools")}
            assert markers == {SYNTHETIC_MARKER}
            assert all("（合成）" in n for n in names)
        finally:
            conn.close()

    def test_demo_years_are_bounded_and_declared(self) -> None:
        """Multi-year synthetic data stays bounded and self-declared."""
        payload = build_payload()
        years = {row["year"] for row in payload["admission_scores"]}
        assert years == set(DEMO_YEARS)
        assert payload["metadata"]["years"] == list(DEMO_YEARS)
        assert payload["metadata"]["latest_year"] == max(DEMO_YEARS)
        assert max(years) - min(years) <= 5

    def test_seed_registers_snapshot_provenance(self, tmp_path: Path) -> None:
        db_path = tmp_path / "demo.db"
        seed_database(db_path, reset=True)
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT dataset_version, profile, source_note, is_synthetic FROM dataset_snapshots"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0]
        assert row[1] == "SYNTHETIC_DEMO"
        assert row[2] and "合成演示数据" in row[2]
        assert row[3] == 1


# ── 2. Session identity (upstream PR #11) ────────────────────────────────
class TestSessionIdentity:
    def test_token_is_bound_to_session(self) -> None:
        from server.auth import create_session_token, verify_session_token

        token = create_session_token("session-a")
        assert verify_session_token("session-a", token)
        assert not verify_session_token("session-b", token)

    def test_untrusted_token_rejected(self) -> None:
        from server.auth import verify_session_token

        assert not verify_session_token("session-a", "deadbeef" * 8)
        assert not verify_session_token("session-a", None)
        assert not verify_session_token("session-a", "")

    def test_token_is_stable_per_session_but_unforgeable(self) -> None:
        """Tokens are HMACs: stable for a session, unguessable without the secret.

        Stability is required so a client can keep calling within its own
        session; unpredictability comes from the keyed HMAC, not from a fresh
        random per call.
        """
        from server.auth import create_session_token

        assert create_session_token("s1") == create_session_token("s1")
        assert create_session_token("s1") != create_session_token("s2")

    def test_chat_refuses_to_mint_token_for_caller_supplied_id(self) -> None:
        """The exact vulnerability fixed in upstream PR #11."""
        import inspect

        from server.routes import chat

        source = inspect.getsource(chat)
        assert "create_session_token(session_id)" in source, (
            "chat must mint the session id it returns, not echo the caller's"
        )
        assert "generate_session_id" in source or "token_urlsafe" in source or ("secrets" in source), (
            "chat must server-generate the session identifier"
        )

    def test_session_ids_are_server_generated(self) -> None:
        import inspect

        from server.routes import chat

        source = inspect.getsource(chat)
        assert "secrets.token_urlsafe" in source, "session identifiers must come from a CSPRNG"


# ── 3. Subsystem state is real, not faked ────────────────────────────────
class TestSubsystemStateIsHonest:
    def test_rag_enabled_by_default_with_synthetic_corpus(self) -> None:
        from server.services import rag

        assert rag.initialize_default() is True
        assert rag.status()["state"] == "RAG_ENABLED"
        assert rag.status()["documents"] >= 1

    def test_health_reports_real_rag_state(self) -> None:
        import asyncio

        from server.routes.health import health_check
        from server.services import rag

        assert rag.initialize_default() is True
        payload = asyncio.run(health_check())
        assert payload["optional_services"]["rag"]["state"] == "RAG_ENABLED"
        assert payload["optional_services"]["rag"]["integrity_ok"] is True
        # An empty local database honestly reports EMPTY; a seeded demo reports
        # SYNTHETIC_DEMO. It must never claim synthetic data it does not have.
        assert payload["data_profile"] in {"SYNTHETIC_DEMO", "EMPTY"}

    def test_health_reports_rag_error_when_disabled(self, monkeypatch) -> None:
        """A switched-off RAG must be visible as degraded, not folded into ok."""
        import asyncio

        from server.routes.health import health_check
        from server.services import rag

        monkeypatch.setenv("ENABLE_RAG_KB", "false")
        rag.reset()
        try:
            payload = asyncio.run(health_check())
            assert payload["optional_services"]["rag"]["state"] == "RAG_DISABLED"
            assert "rag" in payload["degraded_services"]
        finally:
            rag.reset()

    def test_shipped_config_matches_runtime(self) -> None:
        env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        rag_line = next(ln for ln in env.splitlines() if ln.strip().startswith("ENABLE_RAG_KB"))
        assert rag_line.split("=", 1)[1].strip() == "true"
        search_line = next(ln for ln in env.splitlines() if ln.strip().startswith("ENABLE_SEARCH"))
        assert search_line.split("=", 1)[1].strip() == "false"

    def test_skill_safety_rules_present_and_substantial(self) -> None:
        from skills.service import SkillService

        service = SkillService(skills_dir=REPO_ROOT / "skills")
        service.load_assets()
        assert len(service._safety_rules) > 500

    def test_skill_fails_closed_without_safety_rules(self, tmp_path: Path) -> None:
        from skills.service import SkillAssetUnavailableError, SkillService

        with pytest.raises(SkillAssetUnavailableError):
            SkillService(skills_dir=tmp_path).build_context("gaokao")


# ── 4. No excluded assets are shipped ────────────────────────────────────
def _tracked_files() -> list[str]:
    """Files that would actually ship, i.e. git-tracked ones.

    Asserting on the working directory would be wrong: running the suite
    legitimately creates runtime artefacts (data/demo.db, data/reports/) that
    are gitignored and never published. The question that matters is what is
    committed.
    """
    import subprocess

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class TestExcludedAssetsAbsent:
    """Assert on tracked files: this is the release gate for redistribution."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "backups/",
            "data/reports/",
            "prompts/",
            "memory/",
            "content_scripts/",
            "skills/gaokao/expression_engine.md",
            "skills/gaokao/expression_samples.md",
            ".claude/",
            "node_modules/",
        ],
    )
    def test_prefix_not_tracked(self, prefix: str) -> None:
        offenders = [f for f in _tracked_files() if f.startswith(prefix)]
        assert offenders == [], f"{prefix} must not be tracked: {offenders}"

    def test_only_synthetic_demo_corpus_tracked_under_knowledge(self) -> None:
        """``knowledge/`` may ship, but only the marked SYNTHETIC_DEMO corpus.

        The private upstream corpus (persona methodology, quotation collection)
        must not creep back in under the same directory.
        """
        allowed_prefix = "knowledge/synthetic_demo/"
        offenders = [f for f in _tracked_files() if f.startswith("knowledge/") and not f.startswith(allowed_prefix)]
        assert offenders == [], f"only the synthetic demo corpus may ship: {offenders}"

    def test_tracked_corpus_files_are_marked_synthetic(self) -> None:
        marker = "SYNTHETIC_DEMO"
        files = [f for f in _tracked_files() if f.startswith("knowledge/synthetic_demo/groups/")]
        assert files, "the synthetic demo corpus must be tracked"
        for f in files:
            text = (REPO_ROOT / f).read_text(encoding="utf-8")
            assert marker in text, f"{f} must carry the {marker} marker"

    @pytest.mark.parametrize("suffix", [".db", ".sqlite", ".sqlite3", ".sql", ".gz", ".corrupted"])
    def test_no_binary_data_artifacts_tracked(self, suffix: str) -> None:
        offenders = [f for f in _tracked_files() if f.endswith(suffix)]
        assert offenders == [], f"binary data artifacts tracked: {offenders}"

    # Tokens that identify the upstream persona / its old project names. These
    # must never appear in a shipped path, and must not appear in shipped
    # content either — the negative-control tests below are the only allowed
    # exception because they assert the absence.
    _PERSONA_TOKENS = ("zhangxuefeng", "xuefeng", "雪峰")
    _NEGATIVE_CONTROL_TESTS = {
        "tests/test_public_demo_acceptance.py",
        "tests/test_g9_retrieval.py",
        "tests/test_quote_attribution.py",
        "tests/test_voice.py",
        "tests/server/services/test_voice.py",
    }

    # Narrow, audited attribution exceptions. Each entry maps a file to a tuple
    # of (literal, exact expected occurrence count). Before scanning, only these
    # exact literals are masked — everything else in the same file, and any
    # extra copy of the literal, is still scanned. This is deliberately
    # file-scoped and literal-scoped: it does NOT exempt whole files from
    # scanning and does NOT skip whole lines.
    _ALLOWED_ATTRIBUTION_LITERALS = {
        # The canonical original MIT copyright notice (exactly one line).
        "LICENSE": (("Copyright (c) 2026 雪峰Agent Contributors", 1),),
        # The audited upstream source URL (exactly once).
        "THIRD_PARTY_NOTICES.md": (("https://github.com/ziqihe10-droid/xuefeng-agent", 1),),
    }

    @classmethod
    def _mask_allowed_attribution(cls, fname: str, text: str) -> str:
        """Mask only the audited literal(s), enforcing the exact counts.

        A missing or duplicated allowed literal is itself a regression and
        raises ``AssertionError`` (the copyright notice must exist exactly
        once; the upstream URL must appear exactly once).
        """
        out = text
        for literal, expected in cls._ALLOWED_ATTRIBUTION_LITERALS.get(fname, ()):
            found = out.count(literal)
            assert found == expected, (
                f"{fname}: expected exactly {expected} occurrence(s) of {literal!r}, found {found}"
            )
            out = out.replace(literal, "")
        return out

    @classmethod
    def _persona_offenders(cls, fname: str, text: str) -> list[str]:
        masked = cls._mask_allowed_attribution(fname, text)
        low = masked.lower()
        return [t for t in cls._PERSONA_TOKENS if t in low]

    def test_no_named_person_corpus_tracked(self) -> None:
        offenders = [f for f in _tracked_files() if any(t in f.lower() for t in self._PERSONA_TOKENS)]
        assert offenders == [], f"persona-named path must not be tracked: {offenders}"

    def test_no_persona_token_in_shipped_content(self) -> None:
        """No shipped (non-test) file may reference the persona name.

        The only exceptions are the exact, audited attribution literals masked
        by ``_mask_allowed_attribution``; every other byte of every file is
        still scanned.
        """
        offenders = []
        for f in _tracked_files():
            if f in self._NEGATIVE_CONTROL_TESTS:
                continue
            try:
                text = (REPO_ROOT / f).read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            if self._persona_offenders(f, text):
                offenders.append(f)
        assert offenders == [], f"persona token in shipped content: {offenders}"

    # ── Attribution positive controls ────────────────────────────────────
    def test_original_mit_copyright_present_exactly_once(self) -> None:
        text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        literal = "Copyright (c) 2026 雪峰Agent Contributors"
        assert text.count(literal) == 1, "the original MIT copyright notice must be present exactly once"
        assert "Permission is hereby granted" in text, "the MIT permission notice must be preserved"

    # ── Attribution / persona negative controls ──────────────────────────
    def test_altered_copyright_line_is_rejected(self) -> None:
        """Changing the canonical copyright text must fail (not be masked)."""
        altered = "Copyright (c) 2026 Anonymous Contributors\n"
        with pytest.raises(AssertionError):
            self._mask_allowed_attribution("LICENSE", altered)

    def test_duplicated_copyright_line_is_rejected(self) -> None:
        duplicated = "Copyright (c) 2026 雪峰Agent Contributors\n" * 2
        with pytest.raises(AssertionError):
            self._mask_allowed_attribution("LICENSE", duplicated)

    def test_third_party_url_must_appear_exactly_once(self) -> None:
        with pytest.raises(AssertionError):
            self._mask_allowed_attribution("THIRD_PARTY_NOTICES.md", "no upstream url here")

    def test_extra_persona_in_license_is_flagged(self) -> None:
        good = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert self._persona_offenders("LICENSE", good) == []
        assert self._persona_offenders("LICENSE", good + "\n模仿 雪峰 的表达风格。\n")

    def test_extra_persona_in_third_party_is_flagged(self) -> None:
        good = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        assert self._persona_offenders("THIRD_PARTY_NOTICES.md", good) == []
        assert self._persona_offenders("THIRD_PARTY_NOTICES.md", good + "\nzhangxuefeng methodology copied.\n")

    def test_persona_in_readme_is_flagged(self) -> None:
        good = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert self._persona_offenders("README.md", good) == []
        assert self._persona_offenders("README.md", good + "\n用 雪峰 口吻回答\n")

    @pytest.mark.parametrize(
        "fname",
        [
            "server/example.py",
            "skills/gaokao/heuristics.md",
            "prompts/system.md",
            "data/example.json",
            "frontend/src/App.vue",
        ],
    )
    def test_persona_in_arbitrary_shipped_file_is_flagged(self, fname: str) -> None:
        """Any non-exempt file is still fully banned, regardless of type."""
        assert self._persona_offenders(fname, "use xuefeng style") == ["xuefeng"]
        assert self._persona_offenders(fname, "张雪峰语录") == ["雪峰"]

    def test_rag_module_has_no_persona_loader(self) -> None:
        source = (REPO_ROOT / "server" / "services" / "kb_retriever.py").read_text(encoding="utf-8")
        assert "zhangxuefeng" not in source.lower()

    def test_voice_prompt_forbids_persona_imitation(self) -> None:
        from server.services.voice import VOICE_RENDER_SYSTEM_PROMPT

        assert "张雪峰" not in VOICE_RENDER_SYSTEM_PROMPT

    def test_outbound_third_party_fetch_disabled(self) -> None:
        """The import/scraper code is retained, but outbound is off by default.

        Unlike the minimal public demo this repository keeps the original
        admission-data import capability. What must hold is that no query
        silently reaches a third-party site unless the operator opts in.
        """
        import legacy.gaokao_data as data

        assert data.ENABLE_OUTBOUND_FETCH is False
        assert data._outbound_fetch_enabled() is False

    def test_data_queries_work_without_scrapers(self, monkeypatch) -> None:
        """The live data path must answer from the synthetic DB, not the network."""
        from scripts.seed_demo_data import seed_database

        seed_database(REPO_ROOT / "data" / "demo.db", reset=True)

        from legacy import gaokao_data

        # seed_database rebinds db.database in place; force the legacy layer to
        # pick up that session factory instead of one cached by an earlier test.
        monkeypatch.setattr(gaokao_data, "HAS_DB", False, raising=False)
        monkeypatch.setattr(gaokao_data, "_SessionFactory", None, raising=False)
        monkeypatch.setattr(gaokao_data, "_crud", None, raising=False)

        schools = gaokao_data.query_schools_by_major("计算机", "示例省·甲", 600, "物理类")
        assert schools["total"] >= 1, "synthetic data must answer without any outbound call"
        assert gaokao_data.query_match_schools_v2(600, "示例省·甲", "物理", "稳")

    # ── Tracked-file budget ────────────────────────────────────────────
    # The count bound keeps the public distribution small. It is deliberately
    # paired with the type and single-file-size gates below, which are the
    # stronger controls: they reject a bulk corpus or a large binary payload
    # no matter how few files it is split across. A count bound alone would
    # be satisfiable by importing a thousand small corpus files.
    _MAX_TRACKED_FILES = 860
    _MAX_TRACKED_BLOB_BYTES = 10 * 1024 * 1024
    _MAX_SINGLE_FILE_BYTES = 1 * 1024 * 1024
    # Reviewed exceptions to the single-file cap. Each entry names the file and
    # the maximum size that file may reach, so a regenerated artefact cannot
    # silently grow past the audited envelope.
    _ALLOWED_LARGE_FILES = {
        "docs/performance/results/queue-recovery-final.json": 4 * 1024 * 1024,
    }
    # Tracked files must be reviewable source, documentation or configuration.
    # Anything else (data dumps, archives, executables) is a redistribution
    # risk and must be refused at review time, not after publication.
    _ALLOWED_TRACKED_SUFFIXES = frozenset(
        {
            ".conf",
            ".constraints",
            ".css",
            ".example",
            ".html",
            ".ini",
            ".java",
            ".js",
            ".json",
            ".lock",
            ".mako",
            ".md",
            ".png",
            ".py",
            ".sandbox",
            ".sh",
            ".svg",
            ".toml",
            ".ts",
            ".txt",
            ".vue",
            ".xml",
            ".yaml",
            ".yml",
        }
    )
    _ALLOWED_TRACKED_FILENAMES = frozenset(
        {
            ".dockerignore",
            ".gitignore",
            ".gitleaksignore",
            "Dockerfile",
            "LICENSE",
            "Makefile",
        }
    )

    def test_tracked_file_count_is_bounded(self) -> None:
        """Bound the public demo by committed file count, type and size."""
        import subprocess

        # Reviewed baseline: 814 tracked blobs for the governance integration
        # (PRs #215, #217, #218 and #221), well under the 10 MiB byte cap.
        # Growth is allowed for source, tests and governance documents, not
        # for a bulk corpus import; the type, single-file-size, path, corpus,
        # attribution and binary-data gates above remain intact.
        result = subprocess.run(
            ["git", "ls-tree", "-r", "-l", "-z", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
        entries = [entry for entry in result.stdout.split(b"\x00") if entry]
        assert 50 < len(entries) <= self._MAX_TRACKED_FILES
        sizes = []
        for entry in entries:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, _, size = metadata.split()
            assert kind == b"blob" and mode in {b"100644", b"100755"}
            size = int(size)
            sizes.append(size)
            name = raw_path.decode("utf-8")
            suffix = Path(name).suffix.lower()
            assert suffix in self._ALLOWED_TRACKED_SUFFIXES or Path(name).name in self._ALLOWED_TRACKED_FILENAMES, (
                f"tracked file type is not reviewable source, documentation or configuration: {name}"
            )
            allowed = self._ALLOWED_LARGE_FILES.get(name)
            limit = allowed if allowed is not None else self._MAX_SINGLE_FILE_BYTES
            assert size <= limit, f"{name} is {size} bytes, above the {limit} byte limit"
        assert sum(sizes) <= self._MAX_TRACKED_BLOB_BYTES


# ── 5. Session auth round-trip through the real FastAPI app ──────────────
class TestSessionAuthRoundTrip:
    def test_health_endpoint_shape(self) -> None:

        from fastapi.testclient import TestClient

        from server.main import app

        client = TestClient(app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["data_profile"] in {"SYNTHETIC_DEMO", "EMPTY"}
        assert "rag" in body["optional_services"]


class TestImportIsolation:
    """The demo must not resolve modules from anywhere but its own tree.

    A local run once passed only because an excluded module resolved from the
    private original repository sitting in a sibling directory. CI caught it.
    These tests make that failure mode loud rather than silent.
    """

    def test_no_module_resolves_outside_repo_root(self) -> None:
        import sys

        from scripts.seed_demo_data import seed_database  # noqa: F401

        root = str(REPO_ROOT.resolve())
        offenders = []
        for name, module in list(sys.modules.items()):
            if name in ("scripts", "server", "db", "skills", "quality", "legacy"):
                origin = getattr(module, "__file__", None)
                if origin and not str(Path(origin).resolve()).startswith(root):
                    offenders.append(f"{name} -> {origin}")
        assert offenders == [], f"modules imported from outside the repo: {offenders}"

    def test_repo_root_precedes_sys_path(self) -> None:
        import sys

        root = str(REPO_ROOT.resolve())
        assert sys.path[0] == root or root in sys.path[:3], (
            f"repo root must be an early sys.path entry, got {sys.path[:3]}"
        )


# ── 6. Distribution scope of SYNTHETIC_DEMO ──────────────────────────────
class TestPrivateEnvironmentDocsAreMarked:
    """Documents from the private data environment must say that they are.

    The public repository ships only ``SYNTHETIC_DEMO`` synthetic data. The
    historical acceptance / import / planning documents were written against a
    private or authorised data environment and still contain real institution
    counts, score-row counts, coverage ratios and database-backup references.
    Read without that context they look like claims about this repository, so
    every such document must carry a marker near its top.
    """

    _CLAIM = re.compile(r"条分数线|所学校的分数线|30\s*省|全量导入|70,000|\.sql\.gz|数据库备份")

    _MARKER = re.compile(
        r"历史文档"
        r"|历史记录"
        r"|Historical document"
        r"|非公开"
        r"|不代表当前"
        r"|非当前验证结果"
        r"|不代表本仓库"
        r"|虚构数据"
        r"|合成数据"
        r"|SYNTHETIC"
    )

    _HEAD_LINES = 40

    def test_documents_claiming_private_data_scale_are_marked(self) -> None:
        offenders = []
        for path in _tracked_files():
            if not path.endswith(".md"):
                continue
            text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="ignore")
            if not self._CLAIM.search(text):
                continue
            head = "\n".join(text.splitlines()[: self._HEAD_LINES])
            if not self._MARKER.search(head):
                offenders.append(path)
        assert offenders == [], (
            "these documents quote private-environment scale and must state that they are "
            f"historical / non-public: {offenders}"
        )
