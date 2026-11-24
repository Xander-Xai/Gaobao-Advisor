"""Docker-isolated *synthetic* workspace executor for Coding Agent experiments.

Unlike the read-only Gaobao Agent Loop, this executes Python inside real Docker
isolation. It is NOT a hardened multi-tenant boundary and never receives real
admissions data, app secrets, network, Docker socket or a user's home directory.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.agent.sandbox_runtime import SandboxOutputExceeded, _bounded_docker_run, _remove_and_verify_container

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKSPACE_MARKER = ".synthetic_workspace"
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _validated_workspace(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Synthetic workspace itself must not be a symlink")
    root = path.resolve(strict=True)
    marker = root / _WORKSPACE_MARKER
    if not root.is_dir() or not marker.is_file() or marker.is_symlink():
        raise ValueError("Only explicit synthetic scratch workspaces are permitted")
    if root == Path("/") or root == Path.home().resolve() or not root.name.startswith("gaobao-synthetic-"):
        raise ValueError("Refusing host-root or non-scratch workspace")
    if marker.read_text(encoding="utf-8").strip() != "SYNTHETIC_ONLY":
        raise ValueError("Synthetic workspace marker has invalid content")
    if any(char in str(root) for char in (",", "\n", "\r", '"')):
        raise ValueError("Synthetic workspace path contains a mount delimiter")
    # Scratch workspaces should not contain mounts, links, devices or huge files.
    entries = list(root.iterdir())
    if len(entries) > 32:
        raise ValueError("Synthetic workspace contains too many entries")
    for child in entries:
        mode = child.lstat().st_mode
        if (
            child.is_symlink()
            or not stat.S_ISREG(mode)
            or child.stat().st_nlink != 1
            or child.stat().st_size > 2 * 1024 * 1024
        ):
            raise ValueError("Synthetic workspace contains an unsafe entry")
    return root


def _validated_oracle(oracle_dir: Path, workspace: Path) -> Path:
    """The host-owned test oracle must not reside under the agent's RW mount."""
    if oracle_dir.is_symlink():
        raise ValueError("Trusted oracle cannot be a symlink")
    root = oracle_dir.resolve(strict=True)
    if not root.is_dir() or root.is_relative_to(workspace) or workspace.is_relative_to(root):
        raise ValueError("Trusted oracle must be outside the synthetic workspace")
    if any(char in str(root) for char in (",", "\n", "\r", '"')):
        raise ValueError("Trusted oracle path contains a mount delimiter")
    script = root / "runner.py"
    if (
        len(list(root.iterdir())) != 1
        or script.is_symlink()
        or not script.is_file()
        or not stat.S_ISREG(script.lstat().st_mode)
        or script.stat().st_nlink != 1
        or script.stat().st_size > 16384
        or root.stat().st_mode & 0o222
        or script.stat().st_mode & 0o222
    ):
        raise ValueError("Trusted oracle must contain one immutable regular script")
    return root


def sandbox_command(image_id: str, workspace: Path, name: str, *, oracle_dir: Path) -> list[str]:
    """Run a prebuilt image with a writable scratch and an immutable host oracle."""
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError("Only a locally pre-built sha256 Docker image ID is accepted")
    if not re.fullmatch(r"gaobao-harness-[0-9a-f]{16}", name):
        raise ValueError("Invalid sandbox container name")
    root = _validated_workspace(workspace)
    oracle = _validated_oracle(oracle_dir, root)
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "48",
        "--ulimit",
        "nofile=64:64",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--ipc",
        "none",
        "--cpus",
        "1",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m",
        "--mount",
        f"type=bind,src={root},dst=/workspace",
        "--mount",
        f"type=bind,src={oracle},dst=/trusted,readonly",
        "--workdir",
        "/workspace",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint",
        "python",
        image_id,
        "-I",
        "-B",
        "/trusted/runner.py",
    ]


def run_sandbox_probe(
    image_id: str,
    workspace: Path,
    *,
    oracle_source: str | None = None,
    timeout_seconds: int = 25,
    runner: Runner = _bounded_docker_run,
) -> dict[str, Any]:
    """Execute only a trusted oracle in Docker, with untrusted scratch mounted RW.

    The oracle is generated by the host controller outside the agent workspace.
    A model must never control oracle_source. No host shell or credentials enter
    the container, and no raw stdout or stderr enters the structured receipt.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("Sandbox timeout must be 1..60 seconds")
    source = _PROBE if oracle_source is None else oracle_source
    if not isinstance(source, str) or not 1 <= len(source.encode("utf-8")) <= 16384:
        raise ValueError("Trusted oracle source is missing or too large")
    with tempfile.TemporaryDirectory(prefix="gaobao-trusted-oracle-") as parent:
        oracle_dir = Path(parent)
        oracle_file = oracle_dir / "runner.py"
        oracle_file.write_text(source, encoding="utf-8")
        oracle_file.chmod(0o444)
        oracle_dir.chmod(0o555)
        name = "gaobao-harness-" + secrets.token_hex(8)
        try:
            command = sandbox_command(image_id, workspace, name, oracle_dir=oracle_dir)
            report: dict[str, Any] = {}
            try:
                completed = runner(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
                passed = completed.returncode == 0 and "SYNTHETIC_WORKSPACE_PASS" in completed.stdout.splitlines()
                report = {
                    "sandbox": "DOCKER_RESTRICTED_SYNTHETIC",
                    "passed": passed,
                    "exit_code": completed.returncode,
                }
                if completed.returncode == 125:
                    # Debug output is not exported in JSON receipts. CI-only
                    # diagnostic strips both host mount paths and the job name.
                    message = completed.stderr[-500:].replace(str(workspace.resolve()), "<synthetic-scratch>")
                    message = message.replace(str(oracle_dir), "<trusted-oracle>")
                    message = message.replace(name, "<container>")
                    print("sandbox Docker launch diagnostic: " + message, file=sys.stderr)
                    report["error"] = "docker_launch_failed"
            except subprocess.TimeoutExpired:
                report = {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": False, "error": "sandbox_timeout"}
            except SandboxOutputExceeded:
                report = {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": False, "error": "sandbox_output_limit"}
            except OSError:
                report = {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": False, "error": "docker_unavailable"}
            finally:
                # A killed client is not proof that the daemon removed its container.
                if runner is _bounded_docker_run:
                    removed = _remove_and_verify_container(name)
                    if report:
                        report["cleanup_verified"] = removed
                        if not removed:
                            report["passed"] = False
                            report.setdefault("error", "sandbox_cleanup_unverified")
            return report
        finally:
            # Allow the host's temporary-directory cleanup to remove its file.
            oracle_dir.chmod(0o700)


_PROBE = """from pathlib import Path
import os
import socket

root = Path("/workspace")
assert os.getuid() == 65534, "must execute as unprivileged uid"
assert (root / ".synthetic_workspace").read_text().strip() == "SYNTHETIC_ONLY"
assert not Path("/var/run/docker.sock").exists()
for secret in ("OPENAI_API_KEY", "DATABASE_URL", "JWT_SECRET", "SECRET_KEY"):
    assert secret not in os.environ, "container inherited host credential"
try:
    Path("/etc/gaobao-sandbox-probe").write_text("breakout")
except OSError:
    pass
else:
    raise AssertionError("container root filesystem was writable")
(root / "artifact.txt").write_text("sandbox generated artifact\\n")
assert (root / "artifact.txt").read_text().startswith("sandbox")
connection = socket.socket()
connection.settimeout(1)
try:
    connection.connect(("1.1.1.1", 443))
except OSError:
    pass
else:
    raise AssertionError("network access unexpectedly succeeded")
finally:
    connection.close()
print("SYNTHETIC_WORKSPACE_PASS")
"""


def run_sandbox_failure_probes(image_id: str, workspace: Path) -> dict[str, Any]:
    """Run fixed controller-owned negative cases through the real Docker boundary."""
    cases = (
        ("nonzero_exit", "print('SYNTHETIC_WORKSPACE_PASS'); raise SystemExit(7)", 25),
        ("invalid_marker", "print('SYNTHETIC_WORKSPACE_PASS_BUT_FAILED')", 25),
        ("output_limit", "print('X' * 100000)", 25),
        ("timeout", "import time; time.sleep(60)", 10),
    )
    checks = {}
    for name, source, timeout in cases:
        result = run_sandbox_probe(image_id, workspace, oracle_source=source, timeout_seconds=timeout)
        if name == "nonzero_exit":
            correct = result.get("exit_code") == 7
        elif name == "invalid_marker":
            correct = result.get("exit_code") == 0 and "error" not in result
        else:
            correct = result.get("error") == f"sandbox_{name}"
        checks[name] = not result.get("passed") and correct and result.get("cleanup_verified") is True
    return {"passed": all(checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local Docker sha256:image_id, not a mutable tag")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--failure-probes", action="store_true", help="Run fixed real Docker negative probes")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="gaobao-synthetic-parent-") as temp:
        work = Path(temp) / ("gaobao-synthetic-" + secrets.token_hex(6))
        work.mkdir(mode=0o777)
        work.chmod(0o777)  # unprivileged 65534 user can write ONLY this scratch bind mount
        (work / _WORKSPACE_MARKER).write_text("SYNTHETIC_ONLY\n", encoding="utf-8")
        result = run_sandbox_probe(args.image, work)
        result["scratch_artifact_created"] = (work / "artifact.txt").is_file()
        if args.failure_probes:
            result["failure_probes"] = run_sandbox_failure_probes(args.image, work)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    passed = result["passed"] and result["scratch_artifact_created"]
    if args.failure_probes:
        passed = passed and result["failure_probes"]["passed"]
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
