"""POSIX-only local process-group Supervisor for approved synthetic TaskRuns.

Unlike SQLite lease cancellation, this actually signals the LOCAL spawned
worker and its process-group descendants. The supervisor creates the child;
no client-supplied PID, shell command, or arbitrary executable is accepted.
It cannot cancel a provider request already received by a remote server.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from server.agent.taskrun_store import SyntheticTaskRunStore, TaskRunConflict
from server.agent.taskrun_worker import _gate_execution


def _fixed_worker_command(
    store: SyntheticTaskRunStore, run_id: str, model: str | None, permit_external_model: bool
) -> list[str]:
    """Fixed internal module; never accept executable/arguments from user input."""
    command = [
        sys.executable,
        "-m",
        "server.agent.taskrun_worker",
        "--journal",
        str(store.path),
        "--run-id",
        run_id,
    ]
    if permit_external_model:
        command.append("--permit-external-model")
    if model is not None:
        command.extend(["--model", model])
    return command


def _child_exited_unreaped(proc: subprocess.Popen[bytes]) -> bool:
    """Observe child exit without reaping it, preserving its process-group ID."""
    if proc.returncode is not None:
        return True
    try:
        result = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    return result is not None and result.si_pid != 0


def _terminate_owned_group(proc: subprocess.Popen[bytes], *, grace_seconds: float) -> bool:
    """Signal only the process group CREATED by our own Popen(start_new_session).

    Do not call Popen.poll() before signaling: poll() reaps an exited leader,
    while surviving grandchildren may still occupy its process group. WNOWAIT
    lets us observe exit without releasing the PID/PGID before group cleanup.
    """
    if proc.returncode is not None:
        return False
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        proc.wait(timeout=5)
        return False
    time.sleep(grace_seconds)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)
    return True


def supervise_approved_taskrun(
    store: SyntheticTaskRunStore,
    run_id: str,
    *,
    model: str | None = None,
    permit_external_model: bool = False,
    max_seconds: float = 120.0,
    poll_seconds: float = 0.05,
    term_grace_seconds: float = 0.2,
) -> dict[str, Any]:
    """Run a fixed synthetic worker in a new OS session, observing cancel CAS.

    This supervisor is itself a trusted local operator process; user-facing
    HTTP continues using RBAC to decide who may cancel the TaskRun journal.
    Do not launch two supervisors for the same approved TaskRun.
    """
    if os.name != "posix" or not hasattr(os, "killpg") or not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT"):
        raise RuntimeError("Local process-group cancellation requires POSIX waitid/WNOWAIT support")
    if (
        type(max_seconds) not in {int, float}
        or not 0.1 <= max_seconds <= 600
        or type(poll_seconds) not in {int, float}
        or not 0.01 <= poll_seconds <= 1.0
        or type(term_grace_seconds) not in {int, float}
        or not 0.01 <= term_grace_seconds <= 2.0
    ):
        raise ValueError("Unsafe process Supervisor budget")
    task = store.get(run_id)
    _gate_execution(task["engine"], permit_external_model=permit_external_model, model=model)
    if task["status"] != "APPROVED":
        raise TaskRunConflict("Only an approved synthetic TaskRun can be supervised")

    command = _fixed_worker_command(store, run_id, model, permit_external_model)
    # No shell, no arbitrary cwd/PID, no raw stdout from a model process.
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + max_seconds
    reason = "EXITED"
    terminated = False
    try:
        while True:
            current = store.get(run_id)
            # The owner/admin cancel endpoint commits CANCELLED before this
            # branch signals the process group. No direct kill-by-ID endpoint.
            if current["status"] == "CANCELLED":
                reason = "CANCELLED"
                terminated = _terminate_owned_group(child, grace_seconds=term_grace_seconds)
                break
            if _child_exited_unreaped(child):
                reason = "EXITED"
                terminated = _terminate_owned_group(child, grace_seconds=term_grace_seconds)
                break
            if time.monotonic() >= deadline:
                reason = "DEADLINE_EXCEEDED"
                terminated = _terminate_owned_group(child, grace_seconds=term_grace_seconds)
                break
            time.sleep(poll_seconds)
    finally:
        if child.returncode is None:
            _terminate_owned_group(child, grace_seconds=term_grace_seconds)
        child.wait(timeout=5)

    current = store.get(run_id)
    return {
        "run_id": run_id,
        "engine": current["engine"],
        "status": current["status"],
        "supervisor_reason": reason,
        "local_process_group_termination_sent": terminated,
        "worker_exit_code": child.returncode,
        "requires_lease_recovery": reason == "DEADLINE_EXCEEDED" or current["status"] == "RUNNING",
        "remote_provider_cancel_confirmed": False,
        "data_profile": "SYNTHETIC_DEMO",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--model", help="Explicit model ID only when --permit-external-model is also set")
    parser.add_argument("--permit-external-model", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = supervise_approved_taskrun(
            SyntheticTaskRunStore(args.journal),
            args.run_id,
            max_seconds=args.max_seconds,
            model=args.model,
            permit_external_model=args.permit_external_model,
        )
    except (RuntimeError, ValueError, LookupError, OSError) as exc:
        parser.error(type(exc).__name__ + ": synthetic supervision unavailable")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"SUCCEEDED", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
