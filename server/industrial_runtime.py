"""Fail-closed public SQL application deployment contract.

This is a release-candidate guard, not evidence of production acceptance.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping


def industrial_enabled() -> bool:
    return os.getenv("GAOBAO_RUNTIME_PROFILE", "") == "industrial"


def validate_industrial_config(env: Mapping[str, str]) -> None:
    required = {
        "APP_ENV": "production",
        "AUTO_MIGRATE": "false",
        "AUTO_SEED_DEMO": "false",
        "DB_AUTO_CREATE": "false",
        "QUALITY_TASK_MODE": "async",
        "ADMIN_RATE_LIMIT_BACKEND": "redis",
        "ENABLE_RAG_KB": "false",
        "ENABLE_SYNTHETIC_AGENT_EXPERIMENT": "false",
        "ENABLE_OUTBOUND_FETCH": "false",
        "USER_MEMORY_ENABLED": "false",
        "GAOBAO_CHAT_SNAPSHOT_ENABLED": "false",
        "GAOBAO_CHAT_COMPACTION_ENABLED": "false",
        "ADMISSIONS_RESEARCH_ENABLED": "false",
    }
    for name, value in required.items():
        if env.get(name, "").strip().lower() != value:
            raise RuntimeError(f"Industrial profile requires {name}={value}")
    trusted_proxy_raw = env.get("ADMIN_TRUSTED_PROXIES", "").strip() or env.get("TRUSTED_PROXIES", "").strip()
    proxy_networks = [item.strip() for item in trusted_proxy_raw.split(",") if item.strip()]
    if not proxy_networks:
        raise RuntimeError("Industrial profile requires explicit trusted proxy CIDRs")
    for network in proxy_networks:
        try:
            parsed_network = ipaddress.ip_network(network, strict=False)
        except ValueError as exc:
            raise RuntimeError("Industrial profile has an invalid trusted proxy CIDR") from exc
        if parsed_network.prefixlen == 0:
            raise RuntimeError("Industrial profile rejects a world-wide trusted proxy CIDR")
    if not env.get("DATABASE_URL", "").startswith(("mysql://", "mysql+pymysql://")):
        raise RuntimeError("Industrial profile requires explicit MySQL DATABASE_URL")
    if not env.get("REDIS_URL", "").startswith(("redis://", "rediss://")):
        raise RuntimeError("Industrial profile requires explicit REDIS_URL")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", env.get("GAOBAO_DATA_APPROVAL_REF", "")):
        raise RuntimeError("Industrial profile requires a data authorization reference")
    if not re.fullmatch(r"20[0-9]{2}", env.get("GAOBAO_ADMISSIONS_YEAR", "")):
        raise RuntimeError("Industrial profile requires explicit approved admissions year")
    if not env.get("GAOBAO_ADMISSIONS_MANIFEST_PATH", "").strip() or not re.fullmatch(
        r"[a-f0-9]{64}", env.get("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "")
    ):
        raise RuntimeError("Industrial profile requires a pinned admissions manifest")


def redis_ready() -> bool:
    try:
        from server.admin.queue import get_queue

        queue = get_queue()
        return queue is not None and queue.ping()
    except Exception:
        return False


def validate_industrial_dependencies() -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from db.database import engine, get_session
    from db.migrate import alembic_config
    from db.models import AdmissionScore, YiFenYiDuan
    from server.services.admissions_lineage import approved_manifest
    from server.services.data_profile import current_data_profile
    from server.services.industrial_admissions import approved_year

    expected = set(ScriptDirectory.from_config(alembic_config()).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    if len(expected) != 1 or expected != actual:
        raise RuntimeError("Industrial schema is not at the unique Alembic head")
    if current_data_profile() != "CUSTOM":
        raise RuntimeError("Industrial database must contain non-synthetic data")
    year = approved_year()
    approved_manifest(year)
    db = get_session()
    try:
        if not db.query(YiFenYiDuan.id).filter(YiFenYiDuan.year == year).first():
            raise RuntimeError("Approved year has no rank records")
        if not (
            db.query(AdmissionScore.id).filter(AdmissionScore.year == year, AdmissionScore.major_id.is_(None)).first()
        ):
            raise RuntimeError("Approved year has no school-level admission records")
    finally:
        db.close()
    if not redis_ready():
        raise RuntimeError("Industrial Redis unavailable")
