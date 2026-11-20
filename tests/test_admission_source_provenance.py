"""Do not turn fabricated or unverified records into authoritative citations."""

from db.crud import _admission_source_label, query_admission_from_db
from db.models import AdmissionScore, School


def test_fictional_source_is_explicitly_labeled_synthetic():
    school = School(
        name="北辰示例科技大学（合成）",
        province="示例省·甲",
        special_type="SYNTHETIC_DEMO_DATA",
    )
    label = _admission_source_label(school, 2025)
    assert "SYNTHETIC_DEMO" in label
    assert "不得用于真实填报" in label
    assert "官方/省考试院" not in label


def test_unverified_realistic_record_never_claims_official_origin():
    school = School(name="某示范大学", province="某省")
    label = _admission_source_label(school, 2025)
    assert "未核验" in label
    assert "官方/省考试院" not in label


def test_actual_seeded_admission_result_does_not_misattribute_source(admin_factory):
    db = admin_factory()
    try:
        record = db.query(AdmissionScore).first()
        assert record is not None
        school = db.query(School).filter(School.id == record.school_id).one()
        rows = query_admission_from_db(db, school.name, record.province, record.year)
        assert rows
        assert all("SYNTHETIC_DEMO" in row["data_source"] for row in rows)
        assert all("官方/省考试院" not in row["data_source"] for row in rows)
    finally:
        db.close()
