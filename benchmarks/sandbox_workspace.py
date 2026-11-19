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
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKSPACE_MARKER = ".synthetic_workspace"
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _validated_workspace(path: Path) -> Path:
    root = path.resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or not root.joinpath(_WORKSPACE_MARKER).is_file():
        raise ValueError("Only explicit synthetic scratch workspaces are permitted")
    if root.joinpath(_WORKSPACE_MARKER).is_symlink():
        raise ValueError("Synthetic marker must not be a symlink")
    if root == Path("/") or root == Path.home().resolve() or not root.name.startswith("gaobao-synthetic-"):
        raise ValueError("Refusing host-root or non-scratch workspace")
    return root


def sandbox_command(image_id: str, workspace: Path, name: str) -> list[str]:
    """Construct a least-privilege Docker invocation with no host command shell."""
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError("Only a locally pre-built sha256 Docker image ID is accepted")
    if not re.fullmatch(r"gaobao-harness-[0-9a-f]{16}", name):
        raise ValueError("Invalid sandbox container name")
    root = _validated_workspace(workspace)
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
        "--memory",
        "256m",
        "--cpus",
        "1",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m",
        "--mount",
        f"type=bind,src={root},dst=/workspace",
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
        "/workspace/probe.py",
    ]


def run_sandbox_probe(
    image_id: str,
    workspace: Path,
    *,
    timeout_seconds: int = 25,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Run a sandbox probe and clean the container even if Docker times out."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("Sandbox timeout must be 1..60 seconds")
    name = "gaobao-harness-" + secrets.token_hex(8)
    command = sandbox_command(image_id, workspace, name)
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        passed = completed.returncode == 0 and "SYNTHETIC_WORKSPACE_PASS" in completed.stdout
        report = {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": passed, "exit_code": completed.returncode}
        if completed.returncode == 125:
            # Docker launch failed before execution. Show only a short CI-only
            # diagnostic with the synthetic scratch path and container name
            # removed. The exported JSON stays strictly allowlisted.
            message = completed.stderr[-500:].replace(str(workspace.resolve()), "<synthetic-scratch>")
            message = message.replace(name, "<container>")
            print("sandbox Docker launch diagnostic: " + message, file=sys.stderr)
            report["error"] = "docker_launch_failed"
        return report
    except subprocess.TimeoutExpired:
        return {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": False, "error": "sandbox_timeout"}
    except OSError:
        return {"sandbox": "DOCKER_RESTRICTED_SYNTHETIC", "passed": False, "error": "docker_unavailable"}
    finally:
        # --rm does not guarantee cleanup after a client-side timeout; this is
        # intentionally attempted regardless of the outcome. Never shell=True.
        if runner is subprocess.run:
            try:
                subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass


_PROBE = """from pathlib import Path
import socket

root = Path("/workspace")
assert (root / ".synthetic_workspace").read_text().strip() == "SYNTHETIC_ONLY"
assert not Path("/var/run/docker.sock").exists()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local Docker sha256:image_id, not a mutable tag")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="gaobao-synthetic-parent-") as temp:
        work = Path(temp) / ("gaobao-synthetic-" + secrets.token_hex(6))
        work.mkdir(mode=0o777)
        work.chmod(0o777)  # unprivileged 65534 user can write ONLY this scratch bind mount
        (work / _WORKSPACE_MARKER).write_text("SYNTHETIC_ONLY\n", encoding="utf-8")
        (work / "probe.py").write_text(_PROBE, encoding="utf-8")
        result = run_sandbox_probe(args.image, work)
        result["scratch_artifact_created"] = (work / "artifact.txt").is_file()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] and result["scratch_artifact_created"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
