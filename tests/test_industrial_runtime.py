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
        "TRUSTED_PROXIES": "172.28.0.0/24",
        "GAOBAO_DATA_APPROVAL_REF": "internal-review-1",
        "GAOBAO_ADMISSIONS_YEAR": "2025",
        "GAOBAO_ADMISSIONS_MANIFEST_PATH": "/fixture/manifest.json",
        "GAOBAO_ADMISSIONS_MANIFEST_SHA256": "a" * 64,
    }


def test_explicit_config(config):
    validate_industrial_config(config)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_URL", "sqlite:///demo.db"),
        ("REDIS_URL", ""),
        ("TRUSTED_PROXIES", ""),
        ("TRUSTED_PROXIES", "not-a-network"),
        ("TRUSTED_PROXIES", "0.0.0.0/0"),
        ("TRUSTED_PROXIES", "::/0"),
        ("ADMIN_TRUSTED_PROXIES", "192.0.2.0/24"),
        ("GAOBAO_DATA_APPROVAL_REF", ""),
        ("GAOBAO_ADMISSIONS_YEAR", ""),
        ("GAOBAO_ADMISSIONS_MANIFEST_PATH", ""),
        ("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "invalid"),
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


def test_legacy_admin_trusted_proxies_must_match_public_proxy_networks(config):
    config["ADMIN_TRUSTED_PROXIES"] = config["TRUSTED_PROXIES"]
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


def test_industrial_worker_rejects_invalid_config_before_consumption(monkeypatch):
    from server.admin import worker

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(worker, "run_once", lambda **kwargs: pytest.fail("unsafe consumption"))
    monkeypatch.setattr(worker, "run_forever", lambda **kwargs: pytest.fail("unsafe consumption"))
    assert worker.main(["--once"]) == 2
    assert worker.main([]) == 2


def test_industrial_worker_checks_dependencies_before_consumption(monkeypatch, config):
    from server import industrial_runtime
    from server.admin import worker

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    for name, value in config.items():
        monkeypatch.setenv(name, value)

    def unavailable():
        raise RuntimeError("dependency unavailable")

    monkeypatch.setattr(industrial_runtime, "validate_industrial_dependencies", unavailable)
    monkeypatch.setattr(worker, "run_once", lambda **kwargs: pytest.fail("unsafe consumption"))
    assert worker.main(["--once"]) == 2

    calls = []
    monkeypatch.setattr(industrial_runtime, "validate_industrial_dependencies", lambda: calls.append("validated"))

    def consume(**kwargs):
        assert calls == ["validated"]
        return {"status": "idle"}

    monkeypatch.setattr(worker, "run_once", consume)
    assert worker.main(["--once"]) == 0
