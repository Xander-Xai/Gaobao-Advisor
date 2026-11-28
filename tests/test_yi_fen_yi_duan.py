"""Tests for yi_fen_yi_duan import and query functionality."""

import os
import sqlite3

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys_path_dir = os.path.join(PROJECT_ROOT)
import sys  # noqa: E402

if sys_path_dir not in sys.path:
    sys.path.insert(0, sys_path_dir)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "gaokao.db")


@pytest.fixture
def in_memory_db():
    """Create an in-memory database with schema for testing."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()

    # Create admission_scores table (matching the real schema)
    cur.execute("""
        CREATE TABLE admission_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province TEXT NOT NULL,
            year INTEGER NOT NULL,
            subject_type TEXT NOT NULL,
            university TEXT NOT NULL,
            major TEXT,
            min_score INTEGER,
            min_rank INTEGER,
            avg_score REAL,
            max_score INTEGER,
            plan_count INTEGER,
            admit_count INTEGER,
            source TEXT DEFAULT 'baidu_gaokao'
        )
    """)

    # Create yi_fen_yi_duan table (matching the real schema)
    cur.execute("""
        CREATE TABLE yi_fen_yi_duan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province TEXT NOT NULL,
            year INTEGER NOT NULL,
            subject_type TEXT NOT NULL,
            score INTEGER NOT NULL,
            cumulative_count INTEGER NOT NULL,
            UNIQUE(province, year, subject_type, score)
        )
    """)

    # Insert test admission_scores data
    test_data = [
        # (province, year, subject_type, university, major, min_score, min_rank)
        ("广东", 2024, "物理类", "清华", "计算机", 690, 50),
        ("广东", 2024, "物理类", "北大", "数学", 688, 60),
        ("广东", 2024, "物理类", "复旦", "物理", 670, 200),
        ("广东", 2024, "物理类", "浙大", "化学", 650, 800),
        ("广东", 2024, "物理类", "中大", "生物", 620, 5000),
        # Duplicate score with worse rank (should pick better rank)
        ("广东", 2024, "物理类", "华工", "电子", 690, 100),
        # Different year
        ("广东", 2023, "物理类", "清华", "计算机", 685, 55),
        ("广东", 2023, "物理类", "北大", "数学", 680, 80),
        # Different province
        ("北京", 2024, "3+3综合", "清华", "计算机", 695, 30),
        ("北京", 2024, "3+3综合", "北大", "数学", 690, 55),
        # Rows with NULL/0 scores (should be excluded)
        ("河南", 2024, "理科", "郑大", "数学", None, None),
        ("河南", 2024, "理科", "河大", "文学", 0, 0),
    ]
    for row in test_data:
        cur.execute(
            """
            INSERT INTO admission_scores (province, year, subject_type, university, major, min_score, min_rank)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            row,
        )

    conn.commit()
    yield conn
    conn.close()


def _insert_test_rows(conn):
    """Insert test data and return expected count of unique (province, year, subject_type, score) combos."""
    conn.cursor()
    # Expected: 4 unique scores for 广东2024物理类 (690,688,670,650 - 690 deduped)
    # + 2 for 广东2023物理类 + 2 for 北京20243+3综合 = 8 total
    # NULL/0 rows excluded
    expected_count = 8
    return expected_count


class TestPopulateFromAdmissionScores:
    """Tests for populate_from_admission_scores()."""

    def test_inserts_correct_count(self, in_memory_db):
        """Should insert the correct number of unique score-rank records."""
        # Write a temp db file to test the function with file-based path
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # Copy schema and data to temp file
            conn = sqlite3.connect(tmp_path)
            # Create tables
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE admission_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL,
                    university TEXT NOT NULL,
                    major TEXT,
                    min_score INTEGER,
                    min_rank INTEGER,
                    avg_score REAL,
                    max_score INTEGER,
                    plan_count INTEGER,
                    admit_count INTEGER,
                    source TEXT DEFAULT 'baidu_gaokao'
                );
                CREATE TABLE yi_fen_yi_duan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    cumulative_count INTEGER NOT NULL,
                    UNIQUE(province, year, subject_type, score)
                );
            """)
            test_data = [
                ("广东", 2024, "物理类", "清华", "计算机", 690, 50),
                ("广东", 2024, "物理类", "北大", "数学", 688, 60),
                ("广东", 2024, "物理类", "复旦", "物理", 670, 200),
                ("广东", 2024, "物理类", "华工", "电子", 690, 100),  # dup score
                ("广东", 2024, "物理类", "浙大", "化学", 650, 800),
                ("广东", 2023, "物理类", "清华", "计算机", 685, 55),
                ("广东", 2023, "物理类", "北大", "数学", 680, 80),
                ("北京", 2024, "3+3综合", "清华", "计算机", 695, 30),
                ("北京", 2024, "3+3综合", "北大", "数学", 690, 55),
                ("河南", 2024, "理科", "郑大", "数学", None, None),  # excluded
                ("河南", 2024, "理科", "河大", "文学", 0, 0),  # excluded
            ]
            for row in test_data:
                cur.execute(
                    """
                    INSERT INTO admission_scores (province, year, subject_type, university, major, min_score, min_rank)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    row,
                )
            conn.commit()
            conn.close()

            # Run populate
            from scripts.import_yi_fen_yi_duan import populate_from_admission_scores

            inserted = populate_from_admission_scores(db_path=tmp_path)

            # Verify count (4 广东2024 + 2 广东2023 + 2 北京2024 = 8)
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM yi_fen_yi_duan")
            total = cur.fetchone()[0]
            assert total == 8, f"Expected 8 rows, got {total}"
            assert inserted == 8

            # Verify duplicate score 690 picked best rank (50, not 100)
            cur.execute("SELECT cumulative_count FROM yi_fen_yi_duan WHERE province='广东' AND year=2024 AND score=690")
            rank = cur.fetchone()[0]
            assert rank == 50, f"Expected rank 50 (best), got {rank}"

            conn.close()
        finally:
            os.unlink(tmp_path)

    def test_idempotent(self, in_memory_db):
        """Running populate twice should not duplicate rows."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE admission_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL, year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL, university TEXT NOT NULL,
                    major TEXT, min_score INTEGER, min_rank INTEGER,
                    avg_score REAL, max_score INTEGER,
                    plan_count INTEGER, admit_count INTEGER,
                    source TEXT DEFAULT 'baidu_gaokao'
                );
                CREATE TABLE yi_fen_yi_duan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL, year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL, score INTEGER NOT NULL,
                    cumulative_count INTEGER NOT NULL,
                    UNIQUE(province, year, subject_type, score)
                );
            """)
            cur.execute(
                "INSERT INTO admission_scores (province,year,subject_type,university,major,min_score,min_rank) "
                "VALUES ('广东',2024,'物理类','清华','CS',690,50)"
            )
            conn.commit()
            conn.close()

            from scripts.import_yi_fen_yi_duan import populate_from_admission_scores

            first = populate_from_admission_scores(db_path=tmp_path)
            second = populate_from_admission_scores(db_path=tmp_path)

            assert first == 1
            assert second == 0  # idempotent - no new inserts

            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM yi_fen_yi_duan")
            assert cur.fetchone()[0] == 1
            conn.close()
        finally:
            os.unlink(tmp_path)

    def test_query_rank_after_populate(self, in_memory_db):
        """After populating, query yi_fen_yi_duan table directly and get a rank."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE admission_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL, year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL, university TEXT NOT NULL,
                    major TEXT, min_score INTEGER, min_rank INTEGER,
                    avg_score REAL, max_score INTEGER,
                    plan_count INTEGER, admit_count INTEGER,
                    source TEXT DEFAULT 'baidu_gaokao'
                );
                CREATE TABLE yi_fen_yi_duan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL, year INTEGER NOT NULL,
                    subject_type TEXT NOT NULL, score INTEGER NOT NULL,
                    cumulative_count INTEGER NOT NULL,
                    UNIQUE(province, year, subject_type, score)
                );
            """)
            cur.execute(
                "INSERT INTO admission_scores (province,year,subject_type,university,major,min_score,min_rank) "
                "VALUES ('广东',2024,'物理类','清华','CS',690,50)"
            )
            conn.commit()
            conn.close()

            from scripts.import_yi_fen_yi_duan import populate_from_admission_scores

            populate_from_admission_scores(db_path=tmp_path)

            # Query directly
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT cumulative_count FROM yi_fen_yi_duan WHERE province='广东' AND year=2024 AND score=690")
            result = cur.fetchone()
            assert result is not None
            assert result[0] == 50
            conn.close()
        finally:
            os.unlink(tmp_path)


class TestReverseEngineerRankTable:
    """Tests for the existing reverse_engineer_rank_table() function."""

    def test_returns_correct_structure(self):
        """Should return dict keyed by (province, year, subject_type)."""
        from scripts.import_yi_fen_yi_duan import reverse_engineer_rank_table

        rank_table = reverse_engineer_rank_table()
        assert isinstance(rank_table, dict)
        for key in rank_table:
            assert len(key) == 3, f"Key should be (province, year, subject_type), got {key}"

    def test_no_duplicates_in_score_map(self):
        """Each score in the mapping should appear exactly once."""
        from scripts.import_yi_fen_yi_duan import reverse_engineer_rank_table

        rank_table = reverse_engineer_rank_table()
        for _key, score_map in rank_table.items():
            # score_map values should all be positive integers
            for score, rank in score_map.items():
                assert isinstance(score, (int, float))
                assert isinstance(rank, int)
                assert rank > 0


@pytest.fixture
def track_lookup_session(tmp_path, monkeypatch):
    """Use an isolated ORM database for subject-track regressions."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from db.models import AdmissionScore, Base, YiFenYiDuan
    from legacy import gaokao_data

    engine = create_engine(f"sqlite:///{tmp_path / 'bands.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                YiFenYiDuan(province="测试省", year=2025, subject_type="物理类", score=600, cumulative_count=27073),
                YiFenYiDuan(province="测试省", year=2025, subject_type="历史类", score=600, cumulative_count=6004),
                YiFenYiDuan(province="测试省", year=2025, subject_type="物理类", score=590, cumulative_count=30000),
                YiFenYiDuan(province="旧省", year=2024, subject_type="理科", score=600, cumulative_count=2200),
                YiFenYiDuan(province="旧省", year=2024, subject_type="文科", score=600, cumulative_count=400),
                YiFenYiDuan(province="旧省", year=2024, subject_type="综合", score=600, cumulative_count=3784),
                AdmissionScore(
                    school_id=1,
                    province="反推省",
                    year=2024,
                    batch="本科批",
                    subject_type="理科",
                    min_score=600,
                    min_rank=2200,
                ),
                AdmissionScore(
                    school_id=2,
                    province="反推省",
                    year=2024,
                    batch="本科批",
                    subject_type="综合",
                    min_score=600,
                    min_rank=400,
                ),
            ]
        )
        session.commit()

    def factory():
        return Session(engine)

    monkeypatch.setattr(gaokao_data, "_get_session", factory)
    yield gaokao_data.query_yi_fen_yi_duan
    engine.dispose()


def test_rank_lookup_prefers_exact_track(track_lookup_session):
    result = track_lookup_session("测试省", 600, subject_type="物理类", year=2025)
    assert result["rank"] == 27073
    assert result["subject_type"] == "物理类"
    assert result["subject_track_matched"] is True
    assert result["confidence"] == "高"


def test_history_rank_is_not_returned_for_physics_track(track_lookup_session):
    result = track_lookup_session("测试省", 600, subject_type="历史类", year=2025)
    assert result["rank"] == 6004
    assert result["subject_type"] == "历史类"


def test_legacy_track_precedes_broader_composite_track(track_lookup_session):
    result = track_lookup_session("旧省", 600, subject_type="物理类", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"
    assert result["requested_subject_type"] == "物理类"
    assert result["subject_track_matched"] is False
    assert result["confidence"] == "中"


def test_short_physics_alias_uses_legacy_science_track(track_lookup_session):
    result = track_lookup_session("旧省", 600, subject_type="物理", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"


def test_below_band_rank_lookup_is_refused(track_lookup_session):
    result = track_lookup_session("测试省", 100, subject_type="物理类", year=2025)
    assert result["rank"] is None
    assert result["range_min_score"] == 590
    assert result["confidence"] == "无"
    assert "低于" in result["message"]


def test_admission_fallback_keeps_subject_tracks_separate(track_lookup_session):
    result = track_lookup_session("反推省", 600, subject_type="物理类", year=2024)
    assert result["rank"] == 2200
    assert result["subject_type"] == "理科"


def test_admission_fallback_refuses_score_below_range(track_lookup_session):
    result = track_lookup_session("反推省", 100, subject_type="物理类", year=2024)
    assert result["rank"] is None
    assert result["range_min_score"] == 600
    assert result["confidence"] == "无"
