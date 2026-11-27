"""Static regression guard; actual container acceptance is a separate release gate."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_private_environment_files_are_excluded_from_build_context():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert {".env", ".env.*", "**/.env", "**/.env.*"} <= set(ignore)
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert {".env.industrial", ".env.industrial-api"} <= set(gitignore)


def test_industrial_deployment_has_no_local_database_or_direct_api_ports():
    compose = (ROOT / "docker-compose.industrial.yml").read_text()
    for service in ("api", "worker", "java-catalog", "java-gateway", "frontend", "nginx"):
        assert f"  {service}:" in compose
    assert "  mysql:" not in compose
    assert "  redis:" not in compose
    assert compose.count("    ports:") == 1
    assert "127.0.0.1:" in compose
    assert compose.count("entrypoint: []") == 2
    assert 'AUTO_SEED_DEMO: "false"' in compose
    assert 'ENABLE_RAG_KB: "false"' in compose


def test_industrial_worker_and_api_share_admission_profile():
    import yaml

    services = yaml.safe_load((ROOT / "docker-compose.industrial.yml").read_text())["services"]
    api = services["api"]["environment"]
    worker = services["worker"]["environment"]
    assert api["GAOBAO_RUNTIME_PROFILE"] == "industrial"
    assert "TRUSTED_PROXIES" in api and "${TRUSTED_PROXIES:?" in api["TRUSTED_PROXIES"]
    assert all(worker.get(key) == value for key, value in api.items())
    assert "env_file" not in services["worker"]


def test_industrial_manifest_is_shared_read_only():
    import yaml

    services = yaml.safe_load((ROOT / "docker-compose.industrial.yml").read_text())["services"]
    for name in ("api", "worker"):
        service = services[name]
        assert service["environment"]["GAOBAO_ADMISSIONS_MANIFEST_PATH"] == "/run/admissions/manifest.json"
        assert "GAOBAO_ADMISSIONS_MANIFEST_SHA256" in service["environment"]
        assert any(
            "GAOBAO_ADMISSIONS_MANIFEST_HOST_PATH" in volume and volume.endswith(":/run/admissions/manifest.json:ro")
            for volume in service["volumes"]
        )
