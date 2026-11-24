"""Fail-closed public SQL application deployment contract.

This is a release-candidate guard, not evidence of production acceptance.
"""

from __future__ import annotations

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
    if not env.get("DATABASE_URL", "").startswith(("mysql://", "mysql+pymysql://")):
        raise RuntimeError("Industrial profile requires explicit MySQL DATABASE_URL")
    if not env.get("REDIS_URL", "").startswith(("redis://", "rediss://")):
        raise RuntimeError("Industrial profile requires explicit REDIS_URL")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", env.get("GAOBAO_DATA_APPROVAL_REF", "")):
        raise RuntimeError("Industrial profile requires a data authorization reference")


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

    from db.database import engine
    from db.migrate import alembic_config
    from server.services.data_profile import current_data_profile

    expected = set(ScriptDirectory.from_config(alembic_config()).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    if len(expected) != 1 or expected != actual:
        raise RuntimeError("Industrial schema is not at the unique Alembic head")
    if current_data_profile() != "CUSTOM":
        raise RuntimeError("Industrial database must contain non-synthetic data")
    if not redis_ready():
        raise RuntimeError("Industrial Redis unavailable")
