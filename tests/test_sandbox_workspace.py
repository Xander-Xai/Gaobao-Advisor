"""Container command contract; real Docker smoke lives in CI container job."""

import subprocess
from pathlib import Path

import pytest

from benchmarks.sandbox_workspace import run_sandbox_probe, sandbox_command

FAKE_IMAGE = "sha256:" + "a" * 64
FAKE_NAME = "gaobao-harness-" + "b" * 16


@pytest.fixture
def workspace(tmp_path: Path):
    work = tmp_path / "gaobao-synthetic-unit"
    work.mkdir()
    (work / ".synthetic_workspace").write_text("SYNTHETIC_ONLY\n")
    (work / "probe.py").write_text("print('SYNTHETIC_WORKSPACE_PASS')")
    return work


def test_docker_isolated_command_has_no_host_shell_or_network(workspace):
    command = sandbox_command(FAKE_IMAGE, workspace, FAKE_NAME)
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--pull=never" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert "--user" in command
    assert "65534:65534" in command
    assert command[-3:] == [FAKE_IMAGE, "-I", "/workspace/probe.py"]
    assert all("/var/run/docker.sock" not in item for item in command)
    assert not any(item.startswith("--privileged") for item in command)


@pytest.mark.parametrize("image", ["python:3.11", "latest", "sha256:bad", ""])
def test_mutable_or_invalid_image_is_rejected(workspace, image):
    with pytest.raises(ValueError, match="sha256"):
        sandbox_command(image, workspace, FAKE_NAME)


def test_missing_synthetic_marker_refuses_host_directory(tmp_path):
    with pytest.raises(ValueError, match="synthetic"):
        sandbox_command(FAKE_IMAGE, tmp_path, FAKE_NAME)


def test_result_never_exports_raw_container_output(workspace):
    def fake_runner(args, **kwargs):
        assert args[0] == "docker"
        assert kwargs["timeout"] == 25
        return subprocess.CompletedProcess(args, 0, "SYNTHETIC_WORKSPACE_PASS\nsecret=example", "")
    result = run_sandbox_probe(FAKE_IMAGE, workspace, runner=fake_runner)
    assert result == {
        "sandbox": "DOCKER_RESTRICTED_SYNTHETIC",
        "passed": True,
        "exit_code": 0,
    }
    assert "secret" not in str(result)


def test_sandbox_timeout_is_fail_closed(workspace):
    def fake_timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 25)
    result = run_sandbox_probe(FAKE_IMAGE, workspace, runner=fake_timeout)
    assert result["passed"] is False
    assert result["error"] == "sandbox_timeout"
