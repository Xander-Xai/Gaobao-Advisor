"""
CRUD 操作 — 数据库查询层
"""

import json

from sqlalchemy.orm import Session, joinedload

from db.models import (
    AdmissionScore,
    Conversation,
    ConversationMessage,
    EnrollmentPlan,
    Feedback,
    Major,
    School,
    SubjectRanking,
)


def _escape_like(value: str) -> str:
    """转义 SQL LIKE 通配符（%, _），防注入（#13）。"""
    return value.replace("%", "\\%").replace("_", "\\_")


# ── 院校查询 ──


def get_school_by_name(db: Session, name: str) -> School | None:
    """精确匹配院校（支持模糊：先精确，再 LIKE）"""
    school = db.query(School).filter(School.name == name).first()
    if school:
        return school
    # #13: 转义 LIKE 通配符
    safe_name = _escape_like(name)
    return db.query(School).filter(School.name.contains(safe_name, escape="\\")).first()


def get_schools_by_province(db: Session, province: str) -> list:
    return db.query(School).filter(School.province == province).all()


def get_schools_by_level(db: Session, level: str) -> list:
    return db.query(School).filter(School.level == level).all()


# ── 专业查询 ──


def get_major_by_name(db: Session, name: str) -> Major | None:
    major = db.query(Major).filter(Major.name == name).first()
    if major:
        return major
    safe_name = _escape_like(name)
    return db.query(Major).filter(Major.name.contains(safe_name, escape="\\")).first()


def get_hot_majors(db: Session, limit: int = 20) -> list:
    return db.query(Major).filter(Major.is_hot == 1).order_by(Major.avg_salary.desc().nullslast()).limit(limit).all()


def get_majors_by_category(db: Session, category: str) -> list:
    return db.query(Major).filter(Major.category == category).all()


# ── 录取分数线查询 ──


def get_admission_scores(
    db: Session,
    school_id: int | None = None,
    province: str | None = None,
    year: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    subject_type: str | None = None,
    min_score_floor: int | None = None,
    max_score_ceil: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """多条件查询录取分数线"""
    q = (
        db.query(AdmissionScore)
        .options(
            joinedload(AdmissionScore.school),
            joinedload(AdmissionScore.major),
        )
        .join(School, AdmissionScore.school_id == School.id)
    )
    q = q.outerjoin(Major, AdmissionScore.major_id == Major.id)

    if school_id:
        q = q.filter(AdmissionScore.school_id == school_id)
    if province:
        q = q.filter(AdmissionScore.province == province)
    if year:
        q = q.filter(AdmissionScore.year == year)
    if year_from:
        q = q.filter(AdmissionScore.year >= year_from)
    if year_to:
        q = q.filter(AdmissionScore.year <= year_to)
    if subject_type:
        q = q.filter(AdmissionScore.subject_type == subject_type)
    if min_score_floor is not None:
        q = q.filter(AdmissionScore.min_score >= min_score_floor)
    if max_score_ceil is not None:
        q = q.filter(AdmissionScore.min_score <= max_score_ceil)

    rows = q.order_by(AdmissionScore.year.desc(), AdmissionScore.min_score.desc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "school_id": r.school_id,
            "major_id": r.major_id,
            "school_name": r.school.name if r.school else None,
            "major_name": r.major.name if r.major else None,
            "province": r.province,
            "year": r.year,
            "batch": r.batch,
            "subject_type": r.subject_type,
            "min_score": r.min_score,
            "avg_score": r.avg_score,
            "max_score": r.max_score,
            "min_rank": r.min_rank,
            "plan_count": r.plan_count,
        }
        for r in rows
    ]


def get_scores_by_school(db: Session, school_id: int, province: str | None = None, year: int | None = None) -> list:
    q = db.query(AdmissionScore).options(joinedload(AdmissionScore.major)).filter(AdmissionScore.school_id == school_id)
    if province:
        q = q.filter(AdmissionScore.province == province)
    if year:
        q = q.filter(AdmissionScore.year == year)
    return q.order_by(AdmissionScore.year.desc()).all()


# ── 招生计划查询 ──


def get_enrollment_plans(
    db: Session,
    school_id: int | None = None,
    major_id: int | None = None,
    province: str | None = None,
    year: int | None = None,
) -> list:
    q = db.query(EnrollmentPlan)
    if school_id:
        q = q.filter(EnrollmentPlan.school_id == school_id)
    if major_id:
        q = q.filter(EnrollmentPlan.major_id == major_id)
    if province:
        q = q.filter(EnrollmentPlan.province == province)
    if year:
        q = q.filter(EnrollmentPlan.year == year)
    return q.all()


# ── 学科排名查询 ──


def get_subject_rankings(db: Session, school_id: int | None = None, major_category: str | None = None) -> list:
    q = db.query(SubjectRanking)
    if school_id:
        q = q.filter(SubjectRanking.school_id == school_id)
    if major_category:
        q = q.filter(SubjectRanking.major_category == major_category)
    return q.all()


# ── 策略性查询（给 agent 用）──


def _admission_source_label(school: School, year: int) -> str:
    """Describe known provenance without claiming unaudited official origin."""
    is_synthetic = (
        "（合成）" in school.name
        or school.special_type == "SYNTHETIC_DEMO_DATA"
        or "SYNTHETIC" in (school.data_source_note or "").upper()
    )
    if is_synthetic:
        return f"SYNTHETIC_DEMO 合成演示录取数据（{year}年；不得用于真实填报）"
    return f"数据库录取记录（{year}年；原始官方来源未核验）"


def query_admission_from_db(db: Session, school_name: str, province: str, year: int | None = None) -> list[dict]:
    """根据学校名+省份查录取数据，返回格式化结果"""
    school = get_school_by_name(db, school_name)
    if not school:
        return []

    scores = get_scores_by_school(db, school.id, province=province, year=year)
    results = []
    for s in scores:
        major = s.major if s.major_id else None
        results.append(
            {
                "school": school.name,
                "level": school.level,
                "province": s.province,
                "year": s.year,
                "batch": s.batch,
                "subject_type": s.subject_type,
                "min_score": s.min_score,
                "avg_score": s.avg_score,
                "min_rank": s.min_rank,
                "major": major.name if major else "院校线",
                "data_source": _admission_source_label(school, s.year),
            }
        )
    return results


def query_match_schools(db: Session, score: int, province: str, subject_type: str, strategy: str = "稳") -> list[dict]:
    """分数匹配院校推荐（冲/稳/保）"""
    if strategy == "冲":
        lo, hi = score, score + 30
    elif strategy == "保":
        lo, hi = score - 60, score
    else:  # 稳
        lo, hi = score - 20, score + 10

    rows = get_admission_scores(
        db, province=province, subject_type=subject_type, min_score_floor=lo, max_score_ceil=hi, limit=30
    )

    seen = set()
    results = []
    for r in rows:
        sid = r["school_id"]
        if sid in seen:
            continue
        seen.add(sid)
        results.append(r)

    results.sort(key=lambda x: x["min_score"] or 0, reverse=True)
    return results[:15]


# ── 选科适配查询 ──

# 常见专业类别的选科要求（新高考 3+1+2 模式）
# key: 专业类别关键词, value: 必须包含的选科
_MAJOR_SUBJECT_REQUIREMENTS = {
    # 理工类
    "计算机": ["物理"],
    "软件工程": ["物理"],
    "人工智能": ["物理"],
    "电子信息": ["物理"],
    "电气": ["物理"],
    "自动化": ["物理"],
    "机械": ["物理"],
    "土木": ["物理"],
    "建筑": ["物理"],
    "化学": ["化学"],
    "材料": ["化学"],
    "生物": ["生物", "化学"],
    "医学": ["物理", "化学", "生物"],
    "临床医学": ["物理", "化学", "生物"],
    "口腔": ["物理", "化学", "生物"],
    "药学": ["物理", "化学"],
    "护理": ["化学", "生物"],
    "心理学": ["物理", "化学", "生物"],  # 部分院校要求
    "数学": ["物理"],
    "物理学": ["物理"],
    "金融学": [],  # 大部分无选科限制
    "经济学": [],
    "会计": [],
    "法学": [],
    "文学": [],
    "历史": ["历史"],
    "哲学": [],
    "教育学": [],
    "管理": [],
    "市场营销": [],
    "外语": [],
    "新闻": [],
    "传媒": [],
    "艺术": [],
    "体育": [],
}


def get_subject_requirements_for_major(major_name: str) -> list[str]:
    """
    根据专业名称返回选科要求列表。
    返回的是「至少需要包含的选科」之一。
    例如：医学类 → 物化生三选一（实际是必须都选）
    """
    if not major_name:
        return []
    for keyword, reqs in _MAJOR_SUBJECT_REQUIREMENTS.items():
        if keyword in major_name:
            return reqs
    return []


def check_subject_compatibility(
    major_name: str,
    user_subjects: list[str],
) -> dict:
    """
    检查用户的选科是否符合专业要求。

    Args:
        major_name: 专业名称
        user_subjects: 用户已选科目列表，如 ["物理", "化学", "生物"]

    Returns:
        dict: {
            "compatible": bool,
            "required": list[str],  # 该专业要求的选科
            "missing": list[str],   # 用户缺少的选科
            "note": str,            # 解释
        }
    """
    required = get_subject_requirements_for_major(major_name)
    if not required:
        return {
            "compatible": True,
            "required": [],
            "missing": [],
            "note": "该专业无明确选科限制",
        }

    user_set = set(user_subjects)
    required_set = set(required)
    missing = list(required_set - user_set)

    return {
        "compatible": len(missing) == 0,
        "required": required,
        "missing": missing,
        "note": f"需要选考 {'+'.join(required)}"
        if not missing
        else f"需选考 {'+'.join(required)}，你未选 {'+'.join(missing)}",
    }


def get_majors_by_subject_compatibility(
    db: Session,
    user_subjects: list[str],
    category: str | None = None,
) -> list[dict]:
    """
    查询符合用户选科的所有专业。
    返回每个专业及其选科要求、是否匹配。
    """
    q = db.query(Major)
    if category:
        q = q.filter(Major.category == category)
    majors = q.all()

    results = []
    for m in majors:
        compat = check_subject_compatibility(m.name, user_subjects)
        results.append(
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "sub_category": m.sub_category,
                "is_hot": m.is_hot,
                "avg_salary": m.avg_salary,
                "employment_rate": m.employment_rate,
                "compatible": compat["compatible"],
                "required_subjects": compat["required"],
                "note": compat["note"],
            }
        )
    return results


def format_admission_info_db(results: list[dict]) -> str:
    """将数据库查询结果格式化为 Agent 可用文本（带来源标注）"""
    if not results:
        return ""

    lines = ["以下为数据库中的录取参考数据：\n"]
    for i, r in enumerate(results[:5]):
        major_info = r.get("major", "院校线")
        score_info = f"最低分{r['min_score']}" if r.get("min_score") else ""
        rank_info = f"最低位次{r['min_rank']}" if r.get("min_rank") else ""
        source = r.get("data_source", "数据库")
        lines.append(
            f"{i + 1}. {r['school']}({r.get('level', '')}) {major_info} | "
            f"{r['province']} {r['year']}年 {r.get('subject_type', '')} | "
            f"{score_info} {rank_info} | 来源：{source}"
        )
    return "\n".join(lines)


# ── 对话持久化（基于 session_id） ──


def get_or_create_conversation(db: Session, session_id: str) -> Conversation:
    """根据 session_id 查找或创建对话会话。"""
    conv = db.query(Conversation).filter(Conversation.session_id == session_id).first()
    if conv:
        return conv
    conv = Conversation(session_id=session_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def save_message(db: Session, session_id: str, role: str, content: str, *, commit: bool = True) -> None:
    """保存一条对话消息。"""
    conv = get_or_create_conversation(db, session_id)
    msg = ConversationMessage(
        conversation_id=conv.id,
        role=role,
        content=content,
    )
    db.add(msg)
    if commit:
        db.commit()


def save_slots(db: Session, session_id: str, slots: dict, *, commit: bool = True) -> None:
    """更新对话的槽位信息。"""
    conv = get_or_create_conversation(db, session_id)
    conv.slots_json = json.dumps(slots, ensure_ascii=False)

    # 同步核心字段，方便查询
    # 支持两种格式：扁平 {"province": "湖北"} 和嵌套 {"province": {"value": "湖北"}}
    def _val(v):
        if isinstance(v, dict):
            return v.get("value", "") or None
        return v or None

    conv.province = _val(slots.get("province"))
    conv.score_rank = _val(slots.get("score_rank") or slots.get("score"))
    conv.subject = _val(slots.get("subject"))
    if commit:
        db.commit()


def load_conversation_history(db: Session, session_id: str) -> list[dict]:
    """加载对话历史消息（按时间顺序）。"""
    conv = db.query(Conversation).filter(Conversation.session_id == session_id).first()
    if not conv:
        return []
    return [{"role": m.role, "content": m.content} for m in conv.messages]


def load_conversation_slots(db: Session, session_id: str) -> dict | None:
    """加载对话槽位。"""
    conv = db.query(Conversation).filter(Conversation.session_id == session_id).first()
    if not conv or not conv.slots_json:
        return None
    try:
        return json.loads(conv.slots_json)
    except Exception:
        return None


def list_user_conversations(db: Session, limit: int = 10) -> list[Conversation]:
    """列出最近的对话（按更新时间倒序）。"""
    return db.query(Conversation).order_by(Conversation.updated_at.desc()).limit(limit).all()


# ── 用户反馈 ──


def save_feedback(db: Session, session_id: str, message_index: int, rating: str) -> bool:
    """保存一条用户反馈。同一 session + message_index 只保留最新一条。"""
    existing = (
        db.query(Feedback)
        .filter(
            Feedback.session_id == session_id,
            Feedback.message_index == message_index,
        )
        .first()
    )
    if existing:
        existing.rating = rating
    else:
        fb = Feedback(
            session_id=session_id,
            message_index=message_index,
            rating=rating,
        )
        db.add(fb)
    db.commit()
    return True


def get_feedback_stats(db: Session) -> dict:
    """获取反馈统计：好评率、总反馈数。"""
    from sqlalchemy import func

    total = db.query(func.count(Feedback.id)).scalar() or 0
    helpful = db.query(func.count(Feedback.id)).filter(Feedback.rating == "helpful").scalar() or 0
    not_helpful = db.query(func.count(Feedback.id)).filter(Feedback.rating == "not_helpful").scalar() or 0
    return {
        "total": total,
        "helpful": helpful,
        "not_helpful": not_helpful,
        "helpful_rate": f"{helpful / total * 100:.1f}%" if total > 0 else "N/A",
    }
