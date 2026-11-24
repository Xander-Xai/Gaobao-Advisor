"""Container command contract; real Docker smoke lives in CI container job."""

import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.sandbox_workspace import (
    SandboxOutputExceeded,
    _bounded_docker_run,
    run_sandbox_probe,
    sandbox_command,
)

FAKE_IMAGE = "sha256:" + "a" * 64
FAKE_NAME = "gaobao-harness-" + "b" * 16


@pytest.fixture
def workspace(tmp_path: Path):
    work = tmp_path / "gaobao-synthetic-unit"
    work.mkdir()
    (work / ".synthetic_workspace").write_text("SYNTHETIC_ONLY\n")
    (work / "probe.py").write_text("print('SYNTHETIC_WORKSPACE_PASS')")
    return work


@pytest.fixture
def oracle(tmp_path: Path):
    trusted = tmp_path / "trusted-oracle"
    trusted.mkdir()
    source = trusted / "runner.py"
    source.write_text("print('SYNTHETIC_WORKSPACE_PASS')", encoding="utf-8")
    source.chmod(0o444)
    trusted.chmod(0o555)
    try:
        yield trusted
    finally:
        trusted.chmod(0o755)


def test_docker_isolated_command_has_no_host_shell_or_network(workspace, oracle):
    command = sandbox_command(FAKE_IMAGE, workspace, FAKE_NAME, oracle_dir=oracle)
    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--ulimit") + 1] == "nofile=64:64"
    assert "--pull=never" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert "--user" in command
    assert "65534:65534" in command
    assert command[-4:] == [FAKE_IMAGE, "-I", "-B", "/trusted/runner.py"]
    assert "--ipc" in command and command[command.index("--ipc") + 1] == "none"
    assert "--memory-swap" in command and command[command.index("--memory-swap") + 1] == "256m"
    assert f"type=bind,src={oracle},dst=/trusted,readonly" in command
    assert f"type=bind,src={workspace},dst=/workspace" in command
    assert all("/var/run/docker.sock" not in item for item in command)
    assert not any(item.startswith("--privileged") for item in command)


@pytest.mark.parametrize("image", ["python:3.11", "latest", "sha256:bad", ""])
def test_mutable_or_invalid_image_is_rejected(workspace, oracle, image):
    with pytest.raises(ValueError, match="sha256"):
        sandbox_command(image, workspace, FAKE_NAME, oracle_dir=oracle)


def test_missing_synthetic_marker_refuses_host_directory(tmp_path, oracle):
    with pytest.raises(ValueError, match="synthetic"):
        sandbox_command(FAKE_IMAGE, tmp_path, FAKE_NAME, oracle_dir=oracle)


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


def test_oracle_must_be_readonly_and_outside_writable_workspace(workspace, oracle):
    (oracle / "runner.py").chmod(0o644)
    with pytest.raises(ValueError, match="immutable"):
        sandbox_command(FAKE_IMAGE, workspace, FAKE_NAME, oracle_dir=oracle)
    (oracle / "runner.py").chmod(0o444)
    oracle.chmod(0o755)
    (oracle / "runner.py").unlink()
    (oracle / "runner.py").symlink_to(workspace / "probe.py")
    oracle.chmod(0o555)
    with pytest.raises(ValueError, match="immutable"):
        sandbox_command(FAKE_IMAGE, workspace, FAKE_NAME, oracle_dir=oracle)


def test_untrusted_workspace_probe_cannot_replace_controller_oracle(workspace):
    (workspace / "probe.py").write_text("raise SystemExit('fake')", encoding="utf-8")

    def inspect_mounts(command, **kwargs):
        assert command[-1] == "/trusted/runner.py"
        mounts = [arg for arg in command if arg.startswith("type=bind,")]
        assert any("dst=/trusted,readonly" in arg for arg in mounts)
        assert any("dst=/workspace" in arg and "readonly" not in arg for arg in mounts)
        return subprocess.CompletedProcess(command, 0, "SYNTHETIC_WORKSPACE_PASS", "")

    result = run_sandbox_probe(FAKE_IMAGE, workspace, runner=inspect_mounts)
    assert result["passed"]


def test_workspace_hardlink_is_rejected(tmp_path, workspace, oracle):
    file_outside = tmp_path / "outside"
    file_outside.write_text("do not mount", encoding="utf-8")
    (workspace / "hardlink").hardlink_to(file_outside)
    with pytest.raises(ValueError, match="unsafe"):
        sandbox_command(FAKE_IMAGE, workspace, FAKE_NAME, oracle_dir=oracle)


def test_substring_success_marker_cannot_forge_pass(workspace):
    def fake(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "SYNTHETIC_WORKSPACE_PASS_BUT_FAILED\\n", "")

    result = run_sandbox_probe(FAKE_IMAGE, workspace, runner=fake)
    assert result["passed"] is False


def test_bounded_host_pipe_rejects_noisy_untrusted_process():
    # The same bounded subprocess reader is used for the Docker CLI.
    # Without it, an untrusted script can fill HOST RAM, despite Docker memory limits.
    with pytest.raises(SandboxOutputExceeded):
        _bounded_docker_run(
            [sys.executable, "-c", "import sys; sys.stdout.write('X' * 100000)"],
            timeout=5,
        )


def test_bounded_host_pipe_preserves_short_success_output():
    done = _bounded_docker_run(
        [sys.executable, "-c", "print('SYNTHETIC_WORKSPACE_PASS')"],
        timeout=5,
    )
    assert done.returncode == 0
    assert done.stdout.splitlines() == ["SYNTHETIC_WORKSPACE_PASS"]
