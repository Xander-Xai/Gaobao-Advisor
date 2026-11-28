#!/usr/bin/env python3
"""
高考数据模块 v2.0 — 数据库优先 + 百度搜索兜底
数据查询优先级: 本地数据库 → 百度高考 API → 百度搜索兜底
"""

import json
import logging
import os
import sys
from datetime import datetime

from legacy.utils import safe_int as _safe_int

# ── Public demo: outbound third-party fetching is disabled ──────────────
# The upstream project could fall back to scraping a third-party admissions
# site when the local database returned nothing. That is (a) an outbound call
# the demo never intends to make, and (b) silent when it fails, which is the
# same degradation class this repository rejects elsewhere. When disabled the
# queries return the empty result they already had, with an explicit log line.
ENABLE_OUTBOUND_FETCH = os.getenv("ENABLE_OUTBOUND_FETCH", "false").lower() in (
    "true",
    "1",
    "yes",
)

_OUTBOUND_DISABLED_LOGGED = False


def _outbound_fetch_enabled() -> bool:
    global _OUTBOUND_DISABLED_LOGGED
    if not ENABLE_OUTBOUND_FETCH and not _OUTBOUND_DISABLED_LOGGED:
        _OUTBOUND_DISABLED_LOGGED = True
        print(
            "[legacy.gaokao_data] outbound third-party fetch is disabled "
            "(ENABLE_OUTBOUND_FETCH=false); returning local results only.",
            file=sys.stderr,
        )
    return ENABLE_OUTBOUND_FETCH


log = logging.getLogger(__name__)

# 默认数据年份：取最近一个完整年份
DATA_YEAR = datetime.now().year - 1

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 招生政策库（内存字典，教育部官方来源） ──
POLICIES = {
    "强基计划": {
        "title": "强基计划",
        "summary": "教育部自2020年起实施，聚焦高端芯片、智能科技、新材料等关键领域。36所双一流A类高校参与，高考成绩占比不低于85%。",
        "key_points": ["报名时间一般在4月", "只能报考1所高校", "录取在提前批之前", "入校后原则上不得转专业"],
        "source": "教育部阳光高考平台",
    },
    "提前批": {
        "title": "提前批次录取",
        "summary": "在普通批次之前录取，包括军事、公安、公费师范生、定向医学生等。未被录取不影响后续批次。",
        "key_points": ["一般在6月底填报", "不影响后续批次录取", "部分有体检/校测要求"],
        "source": "各省教育考试院",
    },
    "综合评价": {
        "title": "综合评价招生",
        "summary": "综合高考成绩、校测成绩和学业水平考试成绩录取。高考成绩占比不低于60%。",
        "key_points": ["部分高校在部分省份试点", "需要额外申请和参加校测", "代表高校：南科大、上科大、昆山杜克"],
        "source": "各高校招生简章",
    },
    "专项计划": {
        "title": "高校专项计划",
        "summary": "面向农村和脱贫地区学生的定向招生，包括国家专项、地方专项和高校专项。可降5-20分录取。",
        "key_points": ["国家专项面向困难县", "地方专项面向各省农村学生", "高校专项95所高校需单独报名"],
        "source": "教育部高校招生工作规定",
    },
    "新高考": {
        "title": "新高考改革",
        "summary": "取消文理分科，实行3+1+2或3+3模式。选科组合直接影响可报专业范围。",
        "key_points": ["3+1+2：物理/历史二选一+其余四选二", "3+3：六选三", "赋分制按排名百分比赋分"],
        "source": "各省教育厅",
    },
}

# ── 共享常量 ──

# 选科类型别名映射（3+1+2 / 3+3 / 传统文理 全覆盖）
SUBJECT_ALIASES = {
    "物理": ["物理", "物理类", "3+3综合", "综合", "理科"],
    "物理类": ["物理", "物理类", "3+3综合", "综合", "理科"],
    "物": ["物理", "物理类", "3+3综合", "综合", "理科"],
    "历史": ["历史", "历史类", "3+3综合", "综合", "文科"],
    "历史类": ["历史", "历史类", "3+3综合", "综合", "文科"],
    "史": ["历史", "历史类", "3+3综合", "综合", "文科"],
    "理科": ["物理", "物理类", "3+3综合", "综合", "理科"],
    "文科": ["历史", "历史类", "3+3综合", "综合", "文科"],
}

# 冲/稳/保策略的位次调整因子
# 冲: 位次上浮 15%（更激进，位次号更小 = 竞争更激烈）
# 稳: 位次 ±5%~10%（核心填报区）
# 保: 位次下降 25%（兜底，位次号更大 = 更保险）
RANK_FACTORS = {
    "冲": (0.85, 1.00),
    "稳": (0.95, 1.10),
    "保": (1.00, 1.25),
}

# ── 尝试加载数据库层 ──
HAS_DB = False
_SessionFactory = None
_crud = None


def _ensure_db():
    """延迟初始化数据库连接（#7 安全加固: 不再共享全局 session）"""
    global HAS_DB, _SessionFactory, _crud
    if HAS_DB:
        return True
    try:
        from db import crud as _crud_mod
        from db.database import SessionLocal, init_db

        init_db()
        _SessionFactory = SessionLocal
        _crud = _crud_mod
        HAS_DB = True
        return True
    except Exception as e:
        log.debug("数据库初始化失败: %s", e)
        HAS_DB = False
        return False


def _get_session():
    """创建一个新的数据库会话（#7: per-request，非共享）"""
    if not _ensure_db():
        return None
    return _SessionFactory()


def query_enrollment_plan(school_name, province=None, year=None):
    """
    查询招生计划 — 数据库优先，百度高考 API 兜底。
    返回: list of dicts（每条包含 school, major, plan_count, province, year 等）
    异常时返回空列表 []（优雅降级，绝不抛错）。
    """
    if year is None:
        year = DATA_YEAR
    results = []

    # ── 优先查数据库 ──
    db = _get_session()
    if db:
        try:
            school = _crud.get_school_by_name(db, school_name)
            if school:
                plans = _crud.get_enrollment_plans(db, school_id=school.id, province=province, year=year)
                for p in plans:
                    from db.models import Major

                    major = db.query(Major).filter_by(id=p.major_id).first()
                    results.append(
                        {
                            "school": school.name,
                            "major": major.name if major else "未知专业",
                            "province": p.province,
                            "year": p.year,
                            "plan_count": p.plan_count,
                            "subject_requirement": p.subject_requirement,
                            "batch": p.batch,
                            "duration": p.duration,
                            "tuition": p.tuition,
                            "data_source": f"数据库招生计划（{school.name} · {p.year}年）",
                        }
                    )
        except Exception as e:
            log.warning("数据库查询招生计划失败: %s", e)
        finally:
            db.close()

    # ── 数据库没有 → 百度高考 API ──
    if not results and province and _outbound_fetch_enabled():
        try:
            from scrapers.baidu_gaokao import fetch_enrollment_plan

            api_plans = fetch_enrollment_plan(school_name, province, year)
            if api_plans:
                for item in api_plans:
                    results.append(
                        {
                            "school": school_name,
                            "major": item.get("majorName", ""),
                            "province": province,
                            "year": year,
                            "plan_count": _safe_int(item.get("planCount")),
                            "subject_requirement": item.get("subjectRequirement", ""),
                            "batch": item.get("batchName", ""),
                            "duration": _safe_int(item.get("duration")),
                            "tuition": _safe_int(item.get("tuition")),
                            "data_source": f"百度高考 API 招生计划 · {year}年数据",
                        }
                    )
        except Exception as e:
            log.warning("百度高考 API 招生计划查询失败: %s", e)

    return results


def format_enrollment_info(plans):
    """格式化招生计划为 Agent 可用文本（带数据来源标注，优雅处理空数据）。"""
    if not plans:
        return "暂无招生计划数据。建议访问各高校招生网或各省教育考试院查询最新招生计划。"

    lines = []
    for i, p in enumerate(plans[:10], 1):
        school = p.get("school", "")
        major = p.get("major", "")
        province = p.get("province", "")
        year = p.get("year", "")
        batch = p.get("batch", "")
        plan_count = p.get("plan_count", "")
        subject_req = p.get("subject_requirement", "")
        duration = p.get("duration", "")
        tuition = p.get("tuition", "")
        source = p.get("data_source", "")

        line = f"{i}. {school} · {major} | {province} {year}年 {batch}"
        if plan_count:
            line += f" | 招生人数：{plan_count}"
        if subject_req:
            line += f" | {subject_req}"
        if duration:
            line += f" | {duration}年制"
        if tuition:
            line += f" | 学费 {tuition}元/年"
        if source:
            line += f" | 来源：{source}"
        lines.append(line)

    return "\n".join(lines)


def query_admission(school, province, year=None, major=None):
    """
    查询录取数据 — 数据库优先，百度API 兜底
    返回: list of dicts（每条包含 school, province, year, min_score, min_rank, data_source 等）
    异常时返回空列表 []。
    """
    if year is None:
        year = DATA_YEAR
    results = []
    data_source_tier = "T1"  # 跟踪数据来源等级

    # 优先查数据库
    db = _get_session()
    if db:
        try:
            db_results = _crud.query_admission_from_db(db, school, province, year=year)
            if db_results:
                results = db_results
                data_source_tier = "T1"
        except Exception as e:
            log.warning("数据库查询录取数据失败: %s", e)
        finally:
            db.close()

    # 数据库没有 → 百度高考 API（结构化 JSON）
    if not results and _outbound_fetch_enabled():
        try:
            from scrapers.baidu_gaokao import fetch_school_score

            # 根据省份选择正确的 curriculum
            _curriculums = {
                "北京": ["3+3综合"],
                "天津": ["3+3综合"],
                "上海": ["3+3综合"],
                "山东": ["3+3综合"],
                "海南": ["3+3综合"],
                "浙江": ["3+3综合"],
                "四川": ["理科", "文科"],
                "河南": ["理科", "文科"],
                "山西": ["理科", "文科"],
                "陕西": ["理科", "文科"],
                "云南": ["理科", "文科"],
                "内蒙古": ["理科", "文科"],
                "宁夏": ["理科", "文科"],
                "青海": ["理科", "文科"],
                "新疆": ["理科", "文科"],
            }
            # 其他省份默认 3+1+2（物理类/历史类）
            curriculums = _curriculums.get(province, ["物理类", "历史类"])
            for curriculum in curriculums:
                api_scores = fetch_school_score(school, province, year, curriculum)
                if api_scores:
                    data_source_tier = "T2"
                    for s in api_scores:
                        results.append(
                            {
                                "school": school,
                                "level": "",
                                "province": province,
                                "year": year,
                                "batch": s.get("batchName", ""),
                                "subject_type": curriculum,
                                "min_score": _safe_int(s.get("minScore")),
                                "min_rank": _safe_int(s.get("minScoreOrder")),
                                "major": s.get("majorGroup", "院校线"),
                                "data_source": f"百度高考 API · {year}年数据",
                            }
                        )
                    break  # 找到就停
        except Exception as e:
            log.warning("百度高考 API 查询失败: %s", e)

    # 仍然没有 → 老式百度搜索（仅供参考）
    if not results and _outbound_fetch_enabled():
        try:
            from scrapers.baidu import search_admission_snippets

            snippets = search_admission_snippets(school, province, year)
            if snippets:
                data_source_tier = "T3"
            for s in snippets:
                results.append(
                    {
                        "school": school,
                        "province": province,
                        "year": year,
                        "snippet": s[:400],
                        "data_source": f"百度搜索 · {year}年（仅供参考，请以官方数据为准）",
                        "min_score": None,
                        "min_rank": None,
                    }
                )
        except Exception as e:
            log.warning("百度搜索兜底查询失败: %s", e)

    # 标注置信度分数：T1本地数据库=90, T2百度高考API=70, T3百度搜索=40
    _CONFIDENCE_MAP = {"T1": 90, "T2": 70, "T3": 40}
    confidence = _CONFIDENCE_MAP.get(data_source_tier, 30)
    for r in results:
        r.setdefault("confidence_score", confidence)
        r.setdefault("data_tier", data_source_tier)

    return results


def query_yi_fen_yi_duan(province, score, subject_type="物理", year=None):
    """
    查询一分一段表：分数 → 位次
    数据源优先级:
      1. yi_fen_yi_duan 表（真实一分一段表数据，T1 级别，置信度"高"）
      2. admission_scores 表反推（基于录取数据估算，T3 级别，置信度"中-低"）

    返回: dict {rank: 位次, source: 数据来源, confidence: 置信度}
    异常时返回 None。
    """
    if year is None:
        year = DATA_YEAR
    db = _get_session()
    if not db:
        return None

    try:
        # ── 优先：按请求选科精确匹配一分一段表 ──
        from db.models import YiFenYiDuan

        requested = subject_type

        def _band(track):
            return db.query(YiFenYiDuan).filter(
                YiFenYiDuan.province == province,
                YiFenYiDuan.year == year,
                YiFenYiDuan.subject_type == track,
            )

        def _track_candidates():
            priorities = {
                "物理类": ["理科", "物理", "3+3综合", "综合"],
                "物理": ["物理类", "理科", "3+3综合", "综合"],
                "物": ["物理类", "理科", "3+3综合", "综合"],
                "历史类": ["文科", "历史", "3+3综合", "综合"],
                "历史": ["历史类", "文科", "3+3综合", "综合"],
                "史": ["历史类", "文科", "3+3综合", "综合"],
            }
            candidates = priorities.get(requested, SUBJECT_ALIASES.get(requested, [requested]))
            return list(dict.fromkeys(track for track in candidates if track != requested))

        matched = _band(requested).filter(YiFenYiDuan.score == score).first()
        if matched is None:
            for track in _track_candidates():
                matched = _band(track).filter(YiFenYiDuan.score == score).first()
                if matched is not None:
                    break

        if matched is None:
            matched = _band(requested).filter(YiFenYiDuan.score <= score).order_by(YiFenYiDuan.score.desc()).first()
            if matched is None:
                for track in _track_candidates():
                    matched = _band(track).filter(YiFenYiDuan.score <= score).order_by(YiFenYiDuan.score.desc()).first()
                    if matched is not None:
                        break

        if matched is None:
            matched = _band(requested).order_by(YiFenYiDuan.score.asc()).first()
            if matched is None:
                for track in _track_candidates():
                    matched = _band(track).order_by(YiFenYiDuan.score.asc()).first()
                    if matched is not None:
                        break

        if matched:
            exact_track = matched.subject_type == requested
            if matched.score > score:
                return {
                    "rank": None,
                    "score": score,
                    "range_min_score": matched.score,
                    "province": province,
                    "year": year,
                    "subject_type": matched.subject_type,
                    "requested_subject_type": requested,
                    "subject_track_matched": exact_track,
                    "source": f"一分一段表真实数据（{province} {year}年，{matched.subject_type}）",
                    "confidence": "无",
                    "confidence_score": 0,
                    "message": f"分数低于{province} {year}年{matched.subject_type}一分一段表最低分 {matched.score}，无法可靠给出位次",
                }
            return {
                "rank": matched.cumulative_count,
                "score": score,
                "matched_score": matched.score if matched.score != score else None,
                "province": province,
                "year": year,
                "subject_type": matched.subject_type,
                "requested_subject_type": requested,
                "subject_track_matched": exact_track,
                "source": f"一分一段表真实数据（{province} {year}年，{matched.subject_type}）",
                "confidence": "高" if exact_track and matched.score == score else "中",
                "confidence_score": 90 if exact_track and matched.score == score else 60,
            }

        # ── 降级：从 admission_scores 表反推（原有逻辑） ──

        from db.models import AdmissionScore

        query_results = []
        selected_track = requested
        for track in [requested, *_track_candidates()]:
            query_results = (
                db.query(AdmissionScore)
                .filter(
                    AdmissionScore.province == province,
                    AdmissionScore.year == year,
                    AdmissionScore.subject_type == track,
                    AdmissionScore.min_score.isnot(None),
                    AdmissionScore.min_rank.isnot(None),
                )
                .all()
            )
            if query_results:
                selected_track = track
                break

        if not query_results:
            return {
                "rank": None,
                "source": "无数据",
                "confidence": "无",
                "confidence_score": 0,
                "message": f"暂无{province} {year}年{subject_type}的位次数据（建议查省考试院）",
            }

        # 构建分数-位次映射
        score_to_rank = {}
        for r in query_results:
            s = r.min_score
            rank = r.min_rank
            if s not in score_to_rank or rank < score_to_rank[s]:
                score_to_rank[s] = rank

        # 精确匹配
        if score in score_to_rank:
            return {
                "rank": score_to_rank[score],
                "score": score,
                "province": province,
                "year": year,
                "subject_type": selected_track,
                "requested_subject_type": requested,
                "subject_track_matched": selected_track == requested,
                "source": f"本地数据库（基于 {len(query_results)} 条录取数据反推）",
                "confidence": "中（仅供参考）" if selected_track == requested else "低（选科别名回退，仅供参考）",
                "confidence_score": 40 if selected_track == requested else 25,
            }

        # 向下找最接近的分数
        sorted_desc = sorted(score_to_rank.items(), key=lambda x: -x[0])
        for s, r in sorted_desc:
            if s <= score:
                return {
                    "rank": r,
                    "score": score,
                    "matched_score": s,
                    "province": province,
                    "year": year,
                    "subject_type": selected_track,
                    "requested_subject_type": requested,
                    "subject_track_matched": selected_track == requested,
                    "source": f"本地数据库（基于 {len(query_results)} 条录取数据反推，最近匹配 {s}分）",
                    "confidence": "中-低（仅供参考）"
                    if selected_track == requested
                    else "低（选科别名回退，仅供参考）",
                    "confidence_score": 40 if selected_track == requested else 25,
                }

        # 分数低于该选科的全部数据点时拒答，不用最低录取分倒推出位次。
        return {
            "rank": None,
            "score": score,
            "range_min_score": sorted_desc[-1][0],
            "province": province,
            "year": year,
            "subject_type": selected_track,
            "requested_subject_type": requested,
            "subject_track_matched": selected_track == requested,
            "source": f"本地数据库（{selected_track}录取数据）",
            "confidence": "无",
            "confidence_score": 0,
            "message": f"分数低于{province} {year}年{selected_track}录取数据的最低分 {sorted_desc[-1][0]}，无法可靠给出位次",
        }
    except Exception as e:
        log.warning("一分一段查询失败 [%s %s]: %s", province, score, e)
        return None
    finally:
        db.close()


def query_match_schools_v2(score, province, subject_type, strategy="稳", year=None):
    """
    分数匹配院校推荐 v2（基于位次法）
    冲: 位次上浮 5-15%
    稳: 位次 ±5%
    保: 位次下降 10-20%

    先用一分一段算出用户位次，再用位次匹配录取数据
    异常时返回空列表 []。
    """
    if year is None:
        year = DATA_YEAR
    db = _get_session()
    if not db:
        return []

    # Step 1: 算出用户位次
    rank_info = query_yi_fen_yi_duan(province, score, subject_type, year)
    user_rank = rank_info.get("rank") if rank_info else None
    if not user_rank:
        # 降级用原始分数匹配
        try:
            return _crud.query_match_schools(db, score, province, subject_type, strategy)
        finally:
            db.close()

    # Step 2: 根据 strategy 调整位次范围
    lo_factor, hi_factor = RANK_FACTORS.get(strategy, RANK_FACTORS["稳"])
    lo_rank = int(user_rank * lo_factor)
    hi_rank = int(user_rank * hi_factor)

    # Step 3: 查 min_rank 在 [lo_rank, hi_rank] 之间的录取数据
    try:
        from db.models import AdmissionScore, School

        candidates = SUBJECT_ALIASES.get(subject_type, [subject_type])

        rows = (
            db.query(AdmissionScore)
            .join(School)
            .filter(
                AdmissionScore.province == province,
                AdmissionScore.year == year,
                AdmissionScore.subject_type.in_(candidates),
                AdmissionScore.min_rank >= lo_rank,
                AdmissionScore.min_rank <= hi_rank,
                AdmissionScore.min_rank.isnot(None),
            )
            .limit(50)
            .all()
        )

        # 去重（按 school_id），每校取最低分（最易录取）那条
        seen = {}
        for r in rows:
            sid = r.school_id
            if sid not in seen or r.min_rank > seen[sid].min_rank:  # 录取位次最大 = 最好考
                seen[sid] = r

        results = []
        for r in sorted(seen.values(), key=lambda x: x.min_rank or 0, reverse=True):
            results.append(
                {
                    "school_id": r.school_id,
                    "school_name": r.school.name,
                    "school_level": r.school.level,
                    "is_985": r.school.is_985,
                    "is_211": r.school.is_211,
                    "province": r.province,
                    "year": r.year,
                    "batch": r.batch,
                    "subject_type": r.subject_type,
                    "min_score": r.min_score,
                    "min_rank": r.min_rank,
                    "data_source": f"数据库位次法（{year}年{province}{subject_type}，用户位次 {user_rank:,}，策略 {strategy}）",
                }
            )
        return results[:15]
    except Exception as e:
        log.warning("位次法匹配失败，降级到分数匹配: %s", e)
        try:
            return _crud.query_match_schools(db, score, province, subject_type, strategy)
        except Exception as e2:
            log.warning("分数匹配也失败: %s", e2)
            return []
    finally:
        db.close()


def query_match_schools(score, province, subject_type, strategy="稳"):
    """
    分数匹配院校推荐（冲/稳/保）
    返回: list of dicts
    """
    db = _get_session()
    if not db:
        return []
    try:
        return _crud.query_match_schools(db, score, province, subject_type, strategy)
    except Exception as e:
        log.warning("分数匹配查询失败: %s", e)
        return []
    finally:
        db.close()


def query_schools_by_major(major_name, province, score, subject_type="物理", year=None):
    """
    专业→院校反查：找到开设指定专业且录取分数匹配的院校。

    流程：
    1. 在 enrollment_plans 中查找开设该专业的院校（按 major_id 关联）
    2. 在 admission_scores 中验证该校的录取分数
    3. 用位次法按冲/稳/保分组

    Args:
        major_name: 专业名称（支持模糊匹配，如"电气"可匹配"电气工程"）
        province: 用户所在省份
        score: 用户分数
        subject_type: 选科类型
        year: 数据年份，默认最近一年

    Returns:
        dict: {
            "chong": list[dict],
            "wen": list[dict],
            "bao": list[dict],
            "major_name": str,
            "total": int,
        }
    """
    if year is None:
        year = DATA_YEAR
    db = _get_session()
    if not db:
        return {"chong": [], "wen": [], "bao": [], "major_name": major_name, "total": 0}

    try:
        from db.models import AdmissionScore, EnrollmentPlan, Major, School

        # Step 1: 找到匹配的专业
        safe_major = _crud._escape_like(major_name)
        matched_major = db.query(Major).filter(Major.name.contains(safe_major, escape="\\")).first()
        if not matched_major:
            # 尝试更宽泛的匹配：用子类别
            matched_major = db.query(Major).filter(Major.sub_category.contains(safe_major, escape="\\")).first()
        if not matched_major:
            log.info("未找到专业: %s", major_name)
            return {"chong": [], "wen": [], "bao": [], "major_name": major_name, "total": 0}

        major_id = matched_major.id
        actual_major_name = matched_major.name

        # Step 2: 从 enrollment_plans 找到开设该专业的院校 + 录取分数线
        candidates = SUBJECT_ALIASES.get(subject_type, [subject_type])

        # 先获取该专业在指定省份有招生计划的所有 school_id
        plan_school_ids = (
            db.query(EnrollmentPlan.school_id)
            .filter(
                EnrollmentPlan.major_id == major_id,
                EnrollmentPlan.province == province,
                EnrollmentPlan.year == year,
            )
            .distinct()
            .all()
        )
        plan_school_ids = [sid for (sid,) in plan_school_ids]

        if not plan_school_ids:
            # 没有招生计划数据，尝试直接用 admission_scores 中有 major_id 的记录
            log.info("enrollment_plans 无数据，尝试 admission_scores 直查: %s", major_name)

        # Step 3: 用位次法匹配
        rank_info = query_yi_fen_yi_duan(province, score, subject_type, year)
        user_rank = rank_info.get("rank") if rank_info else None

        results_by_strategy = {"冲": [], "稳": [], "保": []}

        if user_rank:
            # 位次法：按 RANK_FACTORS 分三组
            for strategy, (lo_factor, hi_factor) in RANK_FACTORS.items():
                lo_rank = int(user_rank * lo_factor)
                hi_rank = int(user_rank * hi_factor)

                q = (
                    db.query(AdmissionScore)
                    .join(School)
                    .filter(
                        AdmissionScore.province == province,
                        AdmissionScore.year == year,
                        AdmissionScore.subject_type.in_(candidates),
                        AdmissionScore.min_rank >= lo_rank,
                        AdmissionScore.min_rank <= hi_rank,
                        AdmissionScore.min_rank.isnot(None),
                    )
                )

                # 如果有招生计划数据，限定到那些学校
                if plan_school_ids:
                    q = q.filter(AdmissionScore.school_id.in_(plan_school_ids))
                else:
                    # 否则用 major_id 匹配
                    q = q.filter(AdmissionScore.major_id == major_id)

                rows = q.limit(30).all()

                # 去重：每校取位次最大（最容易录取）的那条
                seen = {}
                for r in rows:
                    sid = r.school_id
                    if sid not in seen or r.min_rank > seen[sid].min_rank:
                        seen[sid] = r

                for r in sorted(seen.values(), key=lambda x: x.min_rank or 0, reverse=True)[:8]:
                    results_by_strategy[strategy].append(
                        {
                            "school_id": r.school_id,
                            "school_name": r.school.name,
                            "school_level": r.school.level,
                            "is_985": r.school.is_985,
                            "is_211": r.school.is_211,
                            "province": r.province,
                            "year": r.year,
                            "batch": r.batch,
                            "subject_type": r.subject_type,
                            "min_score": r.min_score,
                            "min_rank": r.min_rank,
                            "major_name": actual_major_name,
                            "data_source": f"数据库位次法·专业反查（{year}年{province}，用户位次 {user_rank:,}，策略 {strategy}）",
                        }
                    )
        else:
            # 没有位次信息，降级用分数区间匹配
            strategy_ranges = {
                "冲": (score, score + 30),
                "稳": (score - 20, score + 10),
                "保": (score - 60, score),
            }
            for strategy, (lo, hi) in strategy_ranges.items():
                q = (
                    db.query(AdmissionScore)
                    .join(School)
                    .filter(
                        AdmissionScore.province == province,
                        AdmissionScore.year == year,
                        AdmissionScore.subject_type.in_(candidates),
                        AdmissionScore.min_score >= lo,
                        AdmissionScore.min_score <= hi,
                        AdmissionScore.min_score.isnot(None),
                    )
                )
                if plan_school_ids:
                    q = q.filter(AdmissionScore.school_id.in_(plan_school_ids))
                else:
                    q = q.filter(AdmissionScore.major_id == major_id)

                rows = q.limit(30).all()
                seen = {}
                for r in rows:
                    sid = r.school_id
                    if sid not in seen or (r.min_rank or 0) > (seen[sid].min_rank or 0):
                        seen[sid] = r

                for r in sorted(seen.values(), key=lambda x: x.min_score or 0, reverse=True)[:8]:
                    results_by_strategy[strategy].append(
                        {
                            "school_id": r.school_id,
                            "school_name": r.school.name,
                            "school_level": r.school.level,
                            "is_985": r.school.is_985,
                            "is_211": r.school.is_211,
                            "province": r.province,
                            "year": r.year,
                            "batch": r.batch,
                            "subject_type": r.subject_type,
                            "min_score": r.min_score,
                            "min_rank": r.min_rank,
                            "major_name": actual_major_name,
                            "data_source": f"数据库分数法·专业反查（{year}年{province}，策略 {strategy}）",
                        }
                    )

        return {
            "chong": results_by_strategy["冲"],
            "wen": results_by_strategy["稳"],
            "bao": results_by_strategy["保"],
            "major_name": actual_major_name,
            "total": sum(len(v) for v in results_by_strategy.values()),
        }
    except Exception as e:
        log.warning("专业→院校反查失败 [%s %s %s %s]: %s", major_name, province, score, subject_type, e)
        return {"chong": [], "wen": [], "bao": [], "major_name": major_name, "total": 0}
    finally:
        db.close()


def format_schools_by_major(result: dict) -> str:
    """将专业→院校反查结果格式化为可展示的 Markdown 文本。"""
    if not result or result.get("total", 0) == 0:
        return f"暂无「{result.get('major_name', '?')}」专业的匹配院校数据。"

    lines = [f"## 开设「{result['major_name']}」专业且分数匹配的院校\n"]

    for group_key, group_label in [
        ("chong", "冲一冲（有风险但值得尝试）"),
        ("wen", "稳一稳（主攻区）"),
        ("bao", "保一保（兜底）"),
    ]:
        schools = result.get(group_key, [])
        if not schools:
            continue
        lines.append(f"### {group_label}\n")
        for i, s in enumerate(schools, 1):
            tags = []
            if s.get("is_985"):
                tags.append("985")
            if s.get("is_211"):
                tags.append("211")
            level_str = " ".join(f"`{t}`" for t in tags) if tags else ""
            score_str = f"最低分 **{s.get('min_score', '?')}** / 位次 **{s.get('min_rank', '?')}**"
            lines.append(
                f"{i}. **{s.get('school_name', '?')}** {level_str}\n"
                f"   {score_str}（{s.get('year', '?')}年 {s.get('batch', '')} {s.get('subject_type', '')}）"
            )
        lines.append("")

    return "\n".join(lines)


def query_school_info(school_name):
    """查询院校基本信息"""
    db = _get_session()
    if not db:
        return None
    try:
        school = _crud.get_school_by_name(db, school_name)
        if school:
            return {
                "name": school.name,
                "province": school.province,
                "city": school.city,
                "level": school.level,
                "school_type": school.school_type,
                "ranking": school.ranking,
                "is_985": school.is_985,
                "is_211": school.is_211,
                "is_double_first_class": school.is_double_first_class,
                "data_source": "数据库（教育部官方名单）",
            }
    except Exception as e:
        log.warning("查询院校信息失败 [%s]: %s", school_name, e)
    finally:
        db.close()
    return None


def query_major_info(major_name):
    """查询专业就业数据"""
    db = _get_session()
    if not db:
        return None
    try:
        major = _crud.get_major_by_name(db, major_name)
        if major:
            return {
                "name": major.name,
                "category": major.category,
                "sub_category": major.sub_category,
                "employment_rate": major.employment_rate,
                "avg_salary": major.avg_salary,
                "median_salary": major.median_salary,
                "job_directions": major.job_directions,
                "is_hot": major.is_hot,
                "data_source": "数据库（麦可思/各校就业质量报告）",
            }
    except Exception as e:
        log.warning("查询专业信息失败 [%s]: %s", major_name, e)
    finally:
        db.close()
    return None


def query_subject_ranking(school_name, category=None):
    """查询学科排名"""
    db = _get_session()
    if not db:
        return []
    try:
        school = _crud.get_school_by_name(db, school_name)
        if school:
            rankings = _crud.get_subject_rankings(db, school_id=school.id, major_category=category)
            return [
                {
                    "school": school.name,
                    "category": r.major_category,
                    "source": r.ranking_source,
                    "year": r.ranking_year,
                    "position": r.ranking_position,
                    "grade": r.grade,
                    "data_source": f"数据库（教育部官方 · {r.ranking_year}年评估）",
                }
                for r in rankings
            ]
    except Exception as e:
        log.warning("查询学科排名失败 [%s]: %s", school_name, e)
    finally:
        db.close()
    return []


def search_policy(keyword):
    """搜索招生政策"""
    matched = []
    for key, policy in POLICIES.items():
        if key in keyword or keyword in key:
            matched.append(policy)
    if not matched:
        for key, policy in POLICIES.items():
            for char in keyword:
                if char in key:
                    matched.append(policy)
                    break
            if matched:
                break
    return matched


def format_admission_info(results):
    """格式化录取数据为 Agent 可用文本（带数据来源标注）"""
    if not results:
        return "暂无该学校录取数据。建议访问各省教育考试院官网查询。"

    lines = []
    for i, r in enumerate(results[:5]):
        source = r.get("data_source", "未知来源")
        conf = r.get("confidence_score", "")
        conf_tag = f" 置信度:{conf}分" if conf else ""

        # 数据库结果（有具体分数）
        if r.get("min_score") is not None:
            major_info = r.get("major", "院校线")
            score_info = f"最低分{r['min_score']}"
            rank_info = f"位次{r['min_rank']}" if r.get("min_rank") else ""
            lines.append(
                f"{i + 1}. {r.get('school', '')}({r.get('level', '')}) {major_info} | "
                f"{r.get('province', '')} {r.get('year', '')}年 {r.get('subject_type', '')} | "
                f"{score_info} {rank_info} | 来源：{source}{conf_tag}"
            )
        # 百度搜索结果（只有文本片段）
        elif r.get("snippet"):
            lines.append(f"{i + 1}. {r['snippet'][:300]} | 来源：{source}{conf_tag}")

    return "\n".join(lines) if lines else "暂无该学校录取数据。"


def get_db_stats():
    """获取数据库统计信息"""
    db = _get_session()
    if not db:
        return {"status": "数据库不可用"}
    try:
        from db.models import AdmissionScore, Major, School, SubjectRanking

        return {
            "schools": db.query(School).count(),
            "majors": db.query(Major).count(),
            "admission_scores": db.query(AdmissionScore).count(),
            "subject_rankings": db.query(SubjectRanking).count(),
            "policies": len(POLICIES),
        }
    except Exception as e:
        log.warning("获取数据库统计失败: %s", e)
        return {"status": "数据库不可用"}
    finally:
        db.close()


# ── 选科适配查询（对外接口） ──


def check_user_subject_compatibility(
    user_subjects: list[str],
    major_name: str | None = None,
) -> dict:
    """
    检查用户选科对指定专业（或查询所有专业）的匹配情况。

    Args:
        user_subjects: 用户选科列表，如 ["物理", "化学", "生物"]
        major_name: 指定专业名；为 None 时返回所有专业的匹配情况

    Returns:
        dict: {
            "user_subjects": list[str],
            "major_name": str | None,
            "compatible": bool | None,
            "required": list[str],
            "missing": list[str],
            "note": str,
            "all_majors": list[dict] | None  # 仅当 major_name=None 时返回
        }
    """
    from db.crud import (
        check_subject_compatibility,
    )

    if not user_subjects:
        return {
            "user_subjects": [],
            "major_name": major_name,
            "compatible": None,
            "required": [],
            "missing": [],
            "note": "未提供选科信息",
        }

    # 指定了具体专业
    if major_name:
        compat = check_subject_compatibility(major_name, user_subjects)
        return {
            "user_subjects": user_subjects,
            "major_name": major_name,
            "compatible": compat["compatible"],
            "required": compat["required"],
            "missing": compat["missing"],
            "note": compat["note"],
        }

    # 未指定专业：返回所有专业的匹配情况
    db = _get_session()
    if not db:
        return {
            "user_subjects": user_subjects,
            "major_name": None,
            "all_majors": [],
            "note": "数据库不可用",
        }
    try:
        all_majors = _crud.get_majors_by_subject_compatibility(db, user_subjects)
        compatible_count = sum(1 for m in all_majors if m["compatible"])
        return {
            "user_subjects": user_subjects,
            "major_name": None,
            "all_majors": all_majors,
            "compatible_count": compatible_count,
            "total_count": len(all_majors),
            "note": f"你已选 {len(user_subjects)} 门：{'+'.join(user_subjects)}，可报专业 {compatible_count}/{len(all_majors)}",
        }
    finally:
        db.close()


def format_subject_compatibility(result: dict) -> str:
    """将选科匹配结果格式化为文本（供 agent 注入 prompt 使用）"""
    if not result.get("user_subjects"):
        return "用户未提供选科信息。"

    if result.get("major_name"):
        # 单专业匹配
        status = "✅ 选科符合" if result.get("compatible") else "❌ 选科不符"
        return (
            f"【选科匹配】{result['major_name']}：{status}\n"
            f"- 你的选科：{'+'.join(result['user_subjects'])}\n"
            f"- 专业要求：{result['note']}\n"
            f"{'- 已选科目符合要求，可放心填报' if result.get('compatible') else '- ⚠️ 缺失：' + '+'.join(result['missing']) + '，不建议填报'}"
        )

    # 全专业列表
    if not result.get("all_majors"):
        return "暂无专业选科匹配数据。"
    lines = [f"【基于你的选科 {','.join(result['user_subjects'])} 的专业匹配】"]
    lines.append(result.get("note", ""))
    for m in result["all_majors"][:15]:
        if m["compatible"] and m["required_subjects"]:
            lines.append(f"  ✅ {m['name']}（{m.get('category', '')}）- 需 {m['note']}")
    return "\n".join(lines)


# ── 志愿表生成器 ──


def generate_volunteer_table(
    score: int,
    province: str,
    subject_type: str = "物理",
    year: int = None,
    chong_count: int = 2,
    wen_count: int = 5,
    bao_count: int = 3,
) -> dict:
    """
    生成结构化志愿表草案。

    Args:
        score: 用户分数
        province: 用户省份
        subject_type: 选科类型
        year: 数据年份
        chong_count: 冲的志愿数
        wen_count: 稳的志愿数
        bao_count: 保的志愿数

    Returns:
        dict: {
            "score": int,
            "province": str,
            "subject_type": str,
            "year": int,
            "rank": int | None,
            "rank_note": str,
            "chong": list[dict],
            "wen": list[dict],
            "bao": list[dict],
            "total": int,
            "disclaimer": str,
        }
    """
    if year is None:
        year = DATA_YEAR
    try:
        # 先算用户位次（缓存结果，避免重复计算）
        rank_info = query_yi_fen_yi_duan(province, score, subject_type, year)
        user_rank = rank_info.get("rank") if rank_info else None
        rank_note = rank_info.get("source", "") + (
            f"（置信度：{rank_info.get('confidence', '')}）" if rank_info else ""
        )

        # 查冲/稳/保三个策略（query_match_schools_v2 内部会再调 query_yi_fen_yi_duan，
        # 但因为数据库会缓存，开销可接受；如需优化可将 user_rank 传入）
        chong_schools = query_match_schools_v2(score, province, subject_type, "冲", year)[:chong_count]
        wen_schools = query_match_schools_v2(score, province, subject_type, "稳", year)[:wen_count]
        bao_schools = query_match_schools_v2(score, province, subject_type, "保", year)[:bao_count]

        # 标注 group
        for s in chong_schools:
            s["group"] = "chong"
        for s in wen_schools:
            s["group"] = "wen"
        for s in bao_schools:
            s["group"] = "bao"

        return {
            "score": score,
            "province": province,
            "subject_type": subject_type,
            "year": year,
            "rank": user_rank,
            "rank_note": rank_note,
            "chong": chong_schools,
            "wen": wen_schools,
            "bao": bao_schools,
            "total": chong_count + wen_count + bao_count,
            "disclaimer": (
                "⚠️ 本志愿表基于往年数据生成，**仅供参考，不构成升学建议**。"
                "请结合各高校 2026 年最新招生章程、省考试院发布的招生计划、"
                "以及个人情况综合判断。"
            ),
        }
    except Exception as e:
        log.warning("生成志愿表失败 [%s %s %s %s]: %s", province, score, subject_type, year, e)
        return {
            "score": score,
            "province": province,
            "subject_type": subject_type,
            "year": year,
            "rank": None,
            "rank_note": "",
            "chong": [],
            "wen": [],
            "bao": [],
            "total": 0,
            "error": f"生成志愿表时出错：{e}",
            "disclaimer": "⚠️ 数据查询异常，无法生成志愿表，请稍后重试。",
        }


def query_admission_trend(school_name, province, subject_type, years=3):
    """
    查询某校近 N 年的录取趋势。

    Args:
        school_name: 学校名称
        province: 省份
        subject_type: 选科类型（物理/历史/综合/理科/文科）
        years: 查询年数（默认3年）

    Returns:
        dict: {
            "school": str,
            "province": str,
            "subject_type": str,
            "data": [{"year": int, "min_score": int|None, "min_rank": int|None}, ...],
            "trend": str,          # "逐年上升" / "逐年下降" / "波动" / "数据不足"
            "trend_detail": str,   # 人类可读的趋势描述
        }
    """
    current_year = datetime.now().year
    db = _get_session()
    if not db:
        return None

    try:
        from db.models import AdmissionScore

        school = _crud.get_school_by_name(db, school_name)
        if not school:
            return None

        candidates = SUBJECT_ALIASES.get(subject_type, [subject_type])
        year_from = current_year - years

        rows = (
            db.query(AdmissionScore)
            .filter(
                AdmissionScore.school_id == school.id,
                AdmissionScore.province == province,
                AdmissionScore.subject_type.in_(candidates),
                AdmissionScore.year >= year_from,
                AdmissionScore.year < current_year,
                AdmissionScore.min_score.isnot(None),
            )
            .order_by(AdmissionScore.year.asc())
            .all()
        )

        if not rows:
            return {
                "school": school.name,
                "province": province,
                "subject_type": subject_type,
                "data": [],
                "trend": "数据不足",
                "trend_detail": f"暂无 {school.name} 近年在{province}的{subject_type}录取数据。",
            }

        # 每年取最高 min_score（最低门槛）那条
        by_year = {}
        for r in rows:
            yr = r.year
            if yr not in by_year or (r.min_score or 0) < (by_year[yr].get("min_score") or 0):
                by_year[yr] = {"year": yr, "min_score": r.min_score, "min_rank": r.min_rank}

        data = [by_year[yr] for yr in sorted(by_year.keys())]

        # 分析趋势（基于 min_score）
        scores = [d["min_score"] for d in data if d["min_score"] is not None]
        if len(scores) < 2:
            trend = "数据不足"
            trend_detail = f"仅 {len(scores)} 年有数据，无法判断趋势。"
        else:
            diffs = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
            all_up = all(d > 0 for d in diffs)
            all_down = all(d < 0 for d in diffs)
            max_diff = max(abs(d) for d in diffs)

            if all_up:
                trend = "逐年上升"
                total_change = scores[-1] - scores[0]
                trend_detail = (
                    f"近 {len(scores)} 年最低分逐年上升（{scores[0]} → {scores[-1]}，共涨 {total_change} 分）。"
                )
            elif all_down:
                trend = "逐年下降"
                total_change = scores[0] - scores[-1]
                trend_detail = (
                    f"近 {len(scores)} 年最低分逐年下降（{scores[0]} → {scores[-1]}，共降 {total_change} 分）。"
                )
            elif max_diff <= 5:
                trend = "基本持平"
                trend_detail = f"近 {len(scores)} 年最低分基本持平（波动 ≤ {max_diff} 分）。"
            else:
                trend = "波动"
                trend_detail = (
                    f"近 {len(scores)} 年最低分波动（{scores[0]} → {scores[-1]}，"
                    f"最大变动 {max_diff} 分），建议关注具体年份数据。"
                )

        return {
            "school": school.name,
            "province": province,
            "subject_type": subject_type,
            "data": data,
            "trend": trend,
            "trend_detail": trend_detail,
        }
    except Exception as e:
        log.warning("历年趋势查询失败 [%s %s %s]: %s", school_name, province, subject_type, e)
        return None
    finally:
        db.close()


def format_volunteer_table(table: dict) -> str:
    """将志愿表格式化为可展示的 Markdown 文本。"""
    if not table:
        return "暂无志愿表数据。"

    lines = []
    lines.append(f"## 📋 志愿表草案（{table['province']} {table['score']}分 {table['subject_type']}）\n")

    if table.get("rank"):
        lines.append(f"**你的预估位次**：约 {table['rank']:,}")
        if table.get("rank_note"):
            lines.append(f"（{table['rank_note']}）")
        lines.append("")

    lines.append("**填报策略**：2 冲 + 5 稳 + 3 保（经典比例）\n")

    for group_key, group_label in [
        ("chong", "🚀 冲一冲（有风险但值得尝试）"),
        ("wen", "🎯 稳一稳（主攻区，重点填报）"),
        ("bao", "🛡️ 保一保（兜底，确保不掉档）"),
    ]:
        schools = table.get(group_key, [])
        if not schools:
            continue
        lines.append(f"### {group_label}\n")
        for i, s in enumerate(schools, 1):
            tags = []
            if s.get("is_985"):
                tags.append("985")
            if s.get("is_211"):
                tags.append("211")
            level_str = " ".join(f"`{t}`" for t in tags) if tags else ""
            score_str = f"去年最低分 **{s.get('min_score', '?')}** / 位次 **{s.get('min_rank', '?')}**"
            lines.append(
                f"{i}. **{s.get('school_name', '?')}** {level_str}\n"
                f"   {score_str}（{s.get('year', '?')}年 {s.get('batch', '')} {s.get('subject_type', '')}）"
            )
        lines.append("")

    if table.get("disclaimer"):
        lines.append("---\n" + table["disclaimer"])

    return "\n".join(lines)


if __name__ == "__main__":
    print("=== 数据模块测试 ===\n")

    stats = get_db_stats()
    print(f"数据库统计: {json.dumps(stats, ensure_ascii=False, indent=2)}\n")

    print("测试: 武汉大学 湖北 2024")
    results = query_admission("武汉大学", "湖北", 2024)
    print(format_admission_info(results))

    print("\n测试: 计算机科学与技术 专业信息")
    info = query_major_info("计算机科学与技术")
    if info:
        print(json.dumps(info, ensure_ascii=False, indent=2))

    print("\n测试: 分数匹配 600分 湖北 物理类 稳")
    matches = query_match_schools(600, "湖北", "物理类", "稳")
    for m in matches[:5]:
        print(f"  {m.get('school_name', '')} - {m.get('min_score', '')}分 {m.get('min_rank', '')}位次")
