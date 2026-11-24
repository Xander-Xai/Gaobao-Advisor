"""
ORM 模型 — 13 张表（院校/专业/分数线/招生计划/学科排名/对话/消息/位次/金句/反馈/考研院校/考研分数线/职业趋势）
"""

import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from db.database import Base


def _utcnow() -> datetime.datetime:
    """Naive UTC timestamp.

    SQLite stores ISO strings and MySQL DATETIME has no timezone; keeping the
    values naive-but-UTC avoids driver-specific tz behaviour while remaining
    unambiguous in the API layer.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class School(Base):
    """院校表"""

    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    province = Column(String(20), nullable=False)
    city = Column(String(30), nullable=False, default="")
    level = Column(String(20), nullable=False, default="普通")  # 985/211/双一流/普通
    school_type = Column(String(20), nullable=False, default="综合")  # 综合/理工/医药/师范/财经
    ranking = Column(Integer, nullable=True)  # 软科排名
    is_985 = Column(Integer, default=0)
    is_211 = Column(Integer, default=0)
    is_double_first_class = Column(Integer, default=0)
    website = Column(String(200), nullable=True)
    description = Column(String(500), nullable=True)
    special_type = Column(
        String(20), nullable=True, default=None
    )  # 军校/艺术类/体育类/港澳院校/新建院校 — 无分数院校分类
    data_source_note = Column(String(200), nullable=True, default=None)  # 数据缺失原因说明

    admission_scores = relationship("AdmissionScore", back_populates="school")
    enrollment_plans = relationship("EnrollmentPlan", back_populates="school")
    subject_rankings = relationship("SubjectRanking", back_populates="school")

    __table_args__ = (
        Index("ix_schools_province", "province"),
        Index("ix_schools_level", "level"),
    )

    def __repr__(self):
        return f"<School({self.name}, {self.level})>"


class Major(Base):
    """专业表"""

    __tablename__ = "majors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(String(50), nullable=False)  # 学科门类: 工学/理学/医学
    sub_category = Column(String(50), nullable=True)  # 专业类: 计算机类/电子信息类
    employment_rate = Column(Float, nullable=True)  # 就业率 0-1
    avg_salary = Column(Float, nullable=True)  # 毕业五年平均月薪（元）
    median_salary = Column(Float, nullable=True)  # 薪资中位数
    salary_range = Column(Text, nullable=True)  # JSON: {"low": x, "high": y}
    top_industries = Column(Text, nullable=True)  # JSON 数组
    employment_locations = Column(Text, nullable=True)  # JSON 数组
    postgraduate_rate = Column(Float, nullable=True)  # 考研比例
    overseas_rate = Column(Float, nullable=True)  # 出国比例
    description = Column(Text, nullable=True)
    job_directions = Column(Text, nullable=True)  # JSON 数组
    is_hot = Column(Integer, default=0)
    education_level = Column(String(10), nullable=True, default="本科")  # 本科/专科

    admission_scores = relationship("AdmissionScore", back_populates="major")
    enrollment_plans = relationship("EnrollmentPlan", back_populates="major")

    __table_args__ = (Index("ix_majors_category", "category"),)

    def __repr__(self):
        return f"<Major({self.name}, {self.category})>"


class AdmissionScore(Base):
    """录取分数线表"""

    __tablename__ = "admission_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=True)  # null = 院校分数线
    province = Column(String(20), nullable=False)
    year = Column(Integer, nullable=False)
    batch = Column(String(20), nullable=False, default="本科一批")
    standardized_batch = Column(String(20), nullable=True)  # 标准化批次名
    subject_type = Column(String(10), nullable=False, default="综合")  # 理工/文史/物理类/历史类
    min_score = Column(Integer, nullable=True)
    avg_score = Column(Float, nullable=True)
    max_score = Column(Integer, nullable=True)
    min_rank = Column(Integer, nullable=True)
    plan_count = Column(Integer, nullable=True)

    school = relationship("School", back_populates="admission_scores")
    major = relationship("Major", back_populates="admission_scores")

    __table_args__ = (
        UniqueConstraint(
            "school_id", "major_id", "province", "year", "batch", "subject_type", name="uq_admission_score"
        ),
        Index("ix_adm_lookup", "school_id", "province", "year", "subject_type"),
        Index("ix_adm_dedup", "school_id", "province", "year", "batch", "subject_type", "major_id"),
        Index("ix_adm_year", "year"),
    )

    def __repr__(self):
        return f"<AdmScore({self.school_id}, {self.province}, {self.year}, {self.min_score})>"


class EnrollmentPlan(Base):
    """招生计划表"""

    __tablename__ = "enrollment_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    major_id = Column(Integer, ForeignKey("majors.id"), nullable=False)
    province = Column(String(20), nullable=False)
    year = Column(Integer, nullable=False)
    plan_count = Column(Integer, nullable=True)
    subject_requirement = Column(String(100), nullable=True)  # 物理必选/不限
    batch = Column(String(20), nullable=True)
    duration = Column(Integer, nullable=True)  # 学制年数
    tuition = Column(Integer, nullable=True)  # 学费元/年

    school = relationship("School", back_populates="enrollment_plans")
    major = relationship("Major", back_populates="enrollment_plans")

    __table_args__ = (UniqueConstraint("school_id", "major_id", "province", "year", name="uq_enrollment_plan"),)


class SubjectRanking(Base):
    """学科排名表"""

    __tablename__ = "subject_rankings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False)
    major_category = Column(String(50), nullable=False)
    ranking_source = Column(String(50), nullable=False)
    ranking_year = Column(Integer, nullable=False)
    ranking_position = Column(Integer, nullable=True)
    grade = Column(String(10), nullable=True)  # A+/A/A-/B+/B/B-/C+/C/C-

    school = relationship("School", back_populates="subject_rankings")

    __table_args__ = (
        UniqueConstraint("school_id", "major_category", "ranking_source", "ranking_year", name="uq_subject_ranking"),
    )


# ── 对话持久化（P3） ──


class Conversation(Base):
    """对话会话表 — 支持关闭页面后恢复"""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    user_label = Column(String(50), nullable=True)  # 用户标签（可选）
    province = Column(String(20), nullable=True)
    score_rank = Column(String(30), nullable=True)
    subject = Column(String(50), nullable=True)
    slots_json = Column(Text, nullable=True)  # 完整 slots JSON
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.id",
    )
    compaction_snapshot = relationship(
        "SessionCompactionSnapshot", back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class ConversationMessage(Base):
    """对话消息表"""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")


class YiFenYiDuan(Base):
    """一分一段表 — 真实位次映射"""

    __tablename__ = "yi_fen_yi_duan"

    id = Column(Integer, primary_key=True, autoincrement=True)
    province = Column(String(20), nullable=False)
    year = Column(Integer, nullable=False)
    subject_type = Column(String(10), nullable=False, default="综合")
    score = Column(Integer, nullable=False)
    cumulative_count = Column(Integer, nullable=False)  # 累计人数（位次）

    __table_args__ = (
        UniqueConstraint("province", "year", "subject_type", "score", name="uq_yi_fen_yi_duan"),
        Index("ix_yfdd_province_year", "province", "year"),
    )

    def __repr__(self):
        return f"<YiFenYiDuan({self.province}, {self.year}, {self.score}分, 位次{self.cumulative_count})>"


class Highlight(Base):
    """金句表 — 高传播力的优质回复片段"""

    __tablename__ = "highlights"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)  # 金句内容
    score = Column(Integer, default=0)  # 自评分数（0-100）
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    def __repr__(self):
        return f"<Highlight({self.session_id}, score={self.score}, {self.content[:20]}...)>"


class Feedback(Base):
    """用户反馈表 — 每条AI回复的有帮助/没帮助评分"""

    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    message_index = Column(Integer, nullable=False)  # 消息在会话中的序号
    rating = Column(String(10), nullable=False)  # "helpful" / "not_helpful"
    quality_score_id = Column(Integer, nullable=True)  # FK to quality_scores
    feedback_text = Column(Text, nullable=True)  # 用户文字反馈
    implicit_negative = Column(Integer, default=0)  # 隐性负面信号计数
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (Index("ix_feedback_session_msg", "session_id", "message_index"),)

    def __repr__(self):
        return f"<Feedback({self.session_id}, #{self.message_index}, {self.rating})>"


class QualityScore(Base):
    """质量评分表 — LLM-as-Judge 四维度评分"""

    __tablename__ = "quality_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), ForeignKey("conversations.session_id"), nullable=False)
    message_id = Column(String(64), nullable=False)
    factual_score = Column(Float, nullable=True)
    relevance_score = Column(Float, nullable=True)
    helpfulness_score = Column(Float, nullable=True)
    style_score = Column(Float, nullable=True)
    aggregate_score = Column(Float, nullable=False)
    hallucination_flags = Column(Text, nullable=True)  # JSON array as text
    judge_model = Column(String(64), nullable=False)
    judge_latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    __table_args__ = (Index("ix_quality_score_conversation", "conversation_id"),)

    def __repr__(self):
        return f"<QualityScore({self.conversation_id}, agg={self.aggregate_score})>"


# ── 考研相关 ──


class GraduateProgram(Base):
    """考研院校专业"""

    __tablename__ = "graduate_program"

    id = Column(Integer, primary_key=True, autoincrement=True)
    school_name = Column(String(100), nullable=False, index=True)
    school_level = Column(String(20))  # 985/211/双一流/普通
    province = Column(String(20))
    major_name = Column(String(100), nullable=False)
    major_category = Column(String(50))
    degree_type = Column(String(20))  # 学硕/专硕
    acceptance_rate = Column(Float)  # 报录比
    avg_score = Column(Integer)  # 平均录取分
    plan_count = Column(Integer)  # 招生计划数

    __table_args__ = (Index("idx_grad_program_school_major", "school_name", "major_name"),)

    def __repr__(self):
        return f"<GraduateProgram({self.school_name}, {self.major_name})>"


class GraduateScore(Base):
    """考研分数线"""

    __tablename__ = "graduate_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, ForeignKey("graduate_program.id"))
    year = Column(Integer, nullable=False)
    subject_type = Column(String(20))
    total_score = Column(Integer)
    politics_score = Column(Integer)
    english_score = Column(Integer)
    major_score = Column(Integer)

    def __repr__(self):
        return f"<GraduateScore(program={self.program_id}, year={self.year}, total={self.total_score})>"


# ── 职业相关 ──


class CareerTrend(Base):
    """职业趋势数据"""

    __tablename__ = "career_trend"

    id = Column(Integer, primary_key=True, autoincrement=True)
    major_name = Column(String(100), index=True)
    industry = Column(String(100))
    job_title = Column(String(100))
    salary_median = Column(Integer)  # 月薪中位数（元）
    employment_rate = Column(Float)  # 就业率
    growth_rate = Column(Float)  # 增长率
    year = Column(Integer)

    def __repr__(self):
        return f"<CareerTrend({self.major_name}, {self.job_title}, {self.salary_median})>"


# ── 运营后台：数据质检 / 审计 / 事务 Outbox ──


class DataQualityJob(Base):
    """数据质检任务 — 状态机 PENDING → RUNNING → SUCCEEDED / FAILED。"""

    __tablename__ = "data_quality_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(32), nullable=False, default="admission_data_quality")
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    review_status = Column(String(24), nullable=False, default="PENDING_REVIEW", index=True)
    idempotency_key = Column(String(80), nullable=False, unique=True)
    filters_json = Column(Text, nullable=False, default="{}")
    requested_by = Column(String(64), nullable=False)
    execution_mode = Column(String(24), nullable=False, default="pending")
    source_marker = Column(String(32), nullable=False, default="SYNTHETIC_DEMO_DATA")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    version = Column(Integer, nullable=False, default=0)
    checked_rows = Column(Integer, nullable=False, default=0)
    issue_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    review_note = Column(String(500), nullable=True)
    reviewed_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    issues = relationship(
        "DataQualityIssue",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="DataQualityIssue.id",
    )

    __table_args__ = (
        Index("ix_dq_jobs_status_created", "status", "created_at"),
        Index("ix_dq_jobs_review", "review_status", "created_at"),
        Index("ix_dq_jobs_actor", "requested_by", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<DataQualityJob({self.id}, {self.status}, {self.issue_count} issues)>"


class DataQualityIssue(Base):
    """质检任务发现的一条问题。"""

    __tablename__ = "data_quality_issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("data_quality_jobs.id"), nullable=False, index=True)
    rule_code = Column(String(40), nullable=False)
    severity = Column(String(12), nullable=False, default="WARNING")
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(Integer, nullable=False, default=0)
    field_name = Column(String(48), nullable=False, default="")
    message = Column(String(300), nullable=False)
    suggestion = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    job = relationship("DataQualityJob", back_populates="issues")

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "rule_code",
            "entity_type",
            "entity_id",
            "field_name",
            name="uq_quality_issue_dedup",
        ),
        Index("ix_dq_issues_job_severity", "job_id", "severity"),
        Index("ix_dq_issues_rule", "job_id", "rule_code"),
    )

    def __repr__(self) -> str:
        return f"<DataQualityIssue({self.rule_code}, {self.severity}, {self.entity_type}:{self.entity_id})>"


class OperationAuditLog(Base):
    """运营操作审计 — 只记录主体、动作、资源与脱敏后的上下文。"""

    __tablename__ = "operation_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, default="")
    actor = Column(String(64), nullable=False)
    role = Column(String(24), nullable=False)
    action = Column(String(64), nullable=False)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(64), nullable=False, default="")
    outcome = Column(String(16), nullable=False, default="SUCCESS")
    detail_json = Column(Text, nullable=True)
    ip_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_actor_created", "actor", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
        Index("ix_audit_request", "request_id"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<OperationAuditLog({self.actor}, {self.action}, {self.outcome})>"


class AdminTokenRevocation(Base):
    """管理员 Token 撤销记录 — 持久化在数据库，Redis 重启不会恢复已撤销 Token。"""

    __tablename__ = "admin_token_revocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jti_hash = Column(String(64), nullable=False, unique=True, index=True)
    username = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<AdminTokenRevocation({self.username}, expires={self.expires_at})>"


class DatasetSnapshot(Base):
    """Public data snapshot metadata.

    Records how the admissions tables were populated so the public data
    explorer can show provenance and generation time without guessing from
    row contents. Only ``scripts.seed_demo_data`` writes this table; imported
    datasets may leave it empty (reported as "unregistered").
    """

    __tablename__ = "dataset_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_version = Column(String(20), nullable=False)
    profile = Column(String(20), nullable=False, default="SYNTHETIC_DEMO")
    source_note = Column(String(200), nullable=True)
    generated_at = Column(DateTime, nullable=False, default=_utcnow)
    year_from = Column(Integer, nullable=True)
    year_to = Column(Integer, nullable=True)
    seed = Column(Integer, nullable=True)
    is_synthetic = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<DatasetSnapshot({self.profile}, {self.dataset_version}, {self.generated_at})>"


class QualityTaskOutbox(Base):
    """事务 Outbox：任务落库与投递解耦，Worker/Dispatcher 可重试。"""

    __tablename__ = "quality_task_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("data_quality_jobs.id"), nullable=False, unique=True)
    topic = Column(String(64), nullable=False, default="quality.jobs")
    payload_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=_utcnow)
    last_error = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    dispatched_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_outbox_status_next", "status", "next_attempt_at"),)

    def __repr__(self) -> str:
        return f"<QualityTaskOutbox(job={self.job_id}, {self.status})>"


class SessionCompactionSnapshot(Base):
    """Opt-in, ephemeral-in-policy summary cache bound to one Conversation row.

    This is untrusted session-context optimization, not an admissions fact
    store or user-profile memory. SQL FK/ORM cascade are deliberate so deleting
    a conversation also removes its cached summary.
    """

    __tablename__ = "session_compaction_snapshots"

    conversation_id = Column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    covered_count = Column(Integer, nullable=False)
    covered_sha256 = Column(String(64), nullable=False)
    policy_sha256 = Column(String(64), nullable=False)
    summary = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=_utcnow)

    conversation = relationship("Conversation", back_populates="compaction_snapshot")

    __table_args__ = (Index("ix_session_compaction_expires", "expires_at"),)
