# Gaobao Agent Sandbox: focused implementation and scope (2026-11-24)

## Project choice (separate employers and timelines)

- **Gaobao-Advisor, later employer**: the only repository where we actively
  develop an execution sandbox. Its existing `benchmarks/sandbox_coding_agent.py`
  can hand *fictional* code-repair tests to a Docker executor.
- **Customer-Service-AI-Agent, earlier employer**: no general shell or Python
  interpreter. Refund/order tools require permission, HITL, tool receipts,
  idempotency, replay protection and checkpoint safety; containerizing a
  business API is not a substitute for these controls.
- **Beauty-Industry-RAG-QA-System, same earlier employer**: retrieval/answer
  permission and evidence gates are relevant; no new code-execution sandbox.

## Pi and OpenCode — inspiration, not false inheritance

- Pi's local coding tools use the **host user privileges by default**.
  Project trust and tool-call hooks are useful, but not operating-system
  isolation. Community extensions can redirect or restrict bash/read/edit/write.
  Source: https://upd.dev/badlogic/pi-mono/src/branch/main/packages/coding-agent/docs/security.md
- OpenCode permission decisions are a policy layer, not automatically an OS
  sandbox; external Docker Sandboxes can run OpenCode itself in isolation and
  proxy credentials. Source: https://docs.docker.com/ai/sandboxes/agents/opencode/
- We borrow **allowlisted tool calls + OS-enforced execution boundary +
  verifiable receipts**, not Pi's or OpenCode's frameworks at runtime.

## Existing implementation (before this PR)

`benchmarks/sandbox_coding_agent.py` accepts `inspect/edit/test/finish`.
Only two fixed fictional arithmetic exercises; model output is a bounded action,
the `edit` action accepts a tiny safe AST; no general shell/SQL, no file
listing outside scratch. The oracle runs inside a disposable Docker container
using a locally pinned image SHA. Agent code cannot issue `docker run`
arguments. Production `/chat` and public admissions APIs do **not** expose it.

`benchmarks/sandbox_workspace.py` already provided: `--network none`,
`--read-only`, `--cap-drop ALL`, `no-new-privileges`, UID 65534,
`--pids-limit 48`, `--memory 256m`, `--cpus 1`, and a temporary writable
synthetic-only bind mount. Container is force-removed on timeout.

## New security acceptance criteria (this PR)

1. **Host output**: streaming Docker-client stdout/stderr has a **64 KiB
   aggregate cap** with fail-closed `sandbox_output_limit`. The limit applies
   to the host process, outside Docker's `--memory` limit.
2. **Runtime caps**: add `--ipc none` and `nofile=64:64`; keep
   no-network, nonroot, read-only root, minimal scratch mount, and no socket.
3. **Real Docker probes**: verify effective UID, missing Docker socket,
   no app/provider credentials in container, failed rootfs write and failed
   external TCP connection, while scratch output still succeeds.
4. **Workspace boundary**: reject symlinks, nested paths, hardlinks,
   nonregular files, oversized files, excessive entries and wrong markers.
5. **Evidence**: success marker must be an **exact output line**, not a
   substring; no raw stdout, stderr or model-generated source in JSON reports.
6. **Failure**: timeout, Docker unavailability, output flood, test fail,
   invalid patch and invalid workspace all report failure, not success.

## Validation

```bash
pytest -q tests/test_sandbox_workspace.py \
  tests/test_sandbox_workspace_governance.py \
  tests/test_sandbox_coding_agent.py --no-cov -o addopts=''
docker build -t gb-advisor:ci .
IMAGE_ID=$(docker image inspect --format '{{.Id}}' gb-advisor:ci)
python -m benchmarks.sandbox_workspace --image "$IMAGE_ID" --out /tmp/gaobao-sandbox.json
python -m benchmarks.sandbox_coding_agent --image "$IMAGE_ID" --out /tmp/gaobao-repair.json
```

The repository's **No-Key Container Smoke Test** runs the two real-Docker
commands. GitHub CI results are bound to the exact PR commit SHA, not inferred
from an older run.

## Non-goals / still not production ready

- No real applicant uploads, user-provided Python, untrusted GitHub clones,
  arbitrary shell, production integration or secrets.
- A Docker container under a shared daemon, using a writable bind mount,
  is **not** a hardened multi-tenant sandbox. Workspaces, models, dependencies
  and host Docker privileges remain trusted for this *synthetic-only* boundary.
- Does not prevent malicious Docker daemon access, kernel escape, or TOCTOU
  races against an adversarial host process. Real user code would require
  stronger isolation (microVM or dedicated pool), authenticated job ownership,
  signed artefact manifests, quotas, cancellation, content scanning, retention
  controls and an explicit rollout plan.
- Synthetic scripted repair success is **not** LLM autonomy, admission
  recommendation quality or production usage evidence.
