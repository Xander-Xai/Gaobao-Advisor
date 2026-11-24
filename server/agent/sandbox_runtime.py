"""Shared bounded local Docker process and cleanup primitives."""

from __future__ import annotations

import os
import selectors
import subprocess
import time

_MAX_OUTPUT_BYTES = 64 * 1024


class SandboxOutputExceeded(RuntimeError):
    """The sandbox process exceeded its bounded host-side output budget."""


def _bounded_docker_run(
    command: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: int = 25,
) -> subprocess.CompletedProcess[str]:
    """Read Docker output incrementally; never capture an unbounded child stream.

    Docker's memory limit covers the *container*, not the host Python process
    reading the Docker client's pipes. A noisy untrusted test can otherwise
    exhaust host RAM before a subprocess.run timeout. Linux/WSL only.
    """
    if os.name != "posix" or check or not capture_output or not text:
        raise ValueError("Bounded executor requires POSIX, text capture, and check=False")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for key, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                selector.register(stream, selectors.EVENT_READ, key)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for event, _ in selector.select(timeout=min(remaining, 0.25)):
                    stream_name = event.data
                    chunk = os.read(event.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    output[stream_name].extend(chunk)
                    if len(output["stdout"]) + len(output["stderr"]) > _MAX_OUTPUT_BYTES:
                        raise SandboxOutputExceeded("Docker subprocess output exceeded limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        code = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(
            command,
            code,
            output["stdout"].decode("utf-8", errors="replace"),
            output["stderr"].decode("utf-8", errors="replace"),
        )
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        process.stdout.close()
        process.stderr.close()


def _remove_and_verify_container(name: str) -> bool:
    """Best-effort removal plus a successful daemon query for this exact name."""
    try:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        listing = subprocess.run(
            ["docker", "ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listing.returncode == 0 and not listing.stdout.strip()
