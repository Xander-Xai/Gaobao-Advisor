"""Configuration admission and dependency health for the industrial profile."""

import pytest

from server.industrial_runtime import validate_industrial_config


@pytest.fixture
def config():
    return {
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
        "DATABASE_URL": "mysql+pymysql://user@mysql/gaobao",
        "REDIS_URL": "redis://redis:6379/0",
        "GAOBAO_DATA_APPROVAL_REF": "internal-review-1",
    }


def test_explicit_config(config):
    validate_industrial_config(config)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_URL", "sqlite:///demo.db"),
        ("REDIS_URL", ""),
        ("GAOBAO_DATA_APPROVAL_REF", ""),
        ("ENABLE_RAG_KB", "true"),
        ("AUTO_SEED_DEMO", "true"),
        ("AUTO_MIGRATE", "true"),
        ("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true"),
        ("ADMISSIONS_RESEARCH_ENABLED", "true"),
        ("GAOBAO_CHAT_SNAPSHOT_ENABLED", "true"),
    ],
)
def test_industrial_rejects_unsafe_configuration(config, name, value):
    config[name] = value
    with pytest.raises(RuntimeError):
        validate_industrial_config(config)


@pytest.mark.asyncio
async def test_industrial_readiness_requires_redis(monkeypatch):
    from server.routes import health

    monkeypatch.setattr(health, "industrial_enabled", lambda: True)
    monkeypatch.setattr(health, "is_db_connected", lambda: True)
    monkeypatch.setattr(health, "redis_ready", lambda: False)
    response = await health.readiness()
    assert response.status_code == 503
    monkeypatch.setattr(health, "redis_ready", lambda: True)
    assert (await health.readiness()).status_code == 200
