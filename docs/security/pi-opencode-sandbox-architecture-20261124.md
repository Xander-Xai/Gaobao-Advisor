# Pi / OpenCode Sandbox review — Gaobao synthetic execution boundary (2026-11-24)

## Decision: only Gaobao needs this sandbox slice

The two earlier-employer projects have different authority:
- **Customer-Service-AI-Agent:** External CRM/order/refund tools and human approval. A
  shell/container sandbox would NOT constrain authorized API side effects, so
  tighten tool permissions, HITL, idempotent execution and replay instead.
- **Beauty-Industry-RAG-QA-System:** Retrieval and RAG inference. Enforce per-user
  RBAC, epoch filters, evidence gates and prompt-injection boundaries, not an
  unrelated code execution API.
- **Gaobao-Advisor (later employer):** Has an *opt-in offline experimental* coding
  Harness, restricted to fictional synthetic exercises. Here a Docker execution
  boundary is useful for proposed Python patches and test runs. Do not enable
  arbitrary Python, shell, Docker, host filesystem or live student data on
  `/api/v1/chat` or the public business LangGraph.

## What Pi and OpenCode actually provide

1. Pi's core agent executes with its launching user's authority by default;
   extensions can replace or guard tool execution, and documented approaches
   include Docker, OpenShell and host-controller / micro-VM delegation.
2. OpenCode has per-tool `allow/ask/deny` permission rules; the project's
   published security policy explicitly says those prompts are **not a sandbox**.
   For OS isolation use a container or VM.
3. Our transferable pattern is **Agent plan -> host tool broker -> isolated
   executor -> bounded observation -> independent acceptance**.
4. The host controls tool policy and independent tests; neither the LLM nor
   an untrusted workspace can overwrite the oracle or grant privileges.

Primary sources:
- https://github.com/earendil-works/pi (Permissions & Containerization)
- https://opencode.ai/docs/permissions
- https://github.com/anomalyco/opencode/security

## Existing implementation and this hardening patch

**Before this patch:** `benchmarks/sandbox_workspace.py` already used a
pinned local Docker image ID, `--network none`, readonly rootfs, non-root
UID, removed Linux capabilities, no new privileges, CPU/memory/pid ceilings,
no Docker socket or API credentials. But the test runner `probe.py` was
stored inside the same writable scratch directory as the model patch.

**Now:** the host controller creates an independent `runner.py` under its
own temporary directory, marks it read-only, and Docker bind-mounts it to
`/trusted` with the **readonly** option. Only the fictional solution and
scratch artifact live under the writable `/workspace` bind mount.
`Dockerfile.sandbox` builds a **separate minimal Python image without
COPY/ADD of the Gaobao repository**. The online API uses its own image;
untrusted synthetic code does not get a read-only copy of application files.
The process entrypoint is fixed as `python -I -B /trusted/runner.py`.
The model cannot choose the entrypoint, mount paths, command, image tag,
network policy, oracle source or Docker arguments.

Additional hardening: disable container IPC, set memory+swap cap together,
reject hard-linked/non-regular scratch files, reject writable/symlinked trusted
oracle files and directories, and fail closed on Docker launch failure or
timeouts. The container test result is accepted only when the trusted test
runner exits zero and emits a fixed success marker; the host verifies the
expected scratch artifact exists. JSON receipts exclude raw stdout/stderr.

## Evidence and limitations

```bash
pytest -q tests/test_sandbox_workspace.py tests/test_sandbox_workspace_governance.py \
  tests/test_sandbox_coding_agent.py tests/test_sandbox_model_planner.py \
  tests/test_sandbox_image_boundary.py --no-cov -o addopts=''
docker build -f Dockerfile.sandbox -t gb-advisor:sandbox-ci .
IMAGE_ID=$(docker image inspect --format '{{.Id}}' gb-advisor:sandbox-ci)
python -m benchmarks.sandbox_workspace --image "$IMAGE_ID" --out /tmp/workspace-eval.json
python -m benchmarks.sandbox_coding_agent --image "$IMAGE_ID" --out /tmp/coding-eval.json
# CI additionally builds the repository Docker image and executes two
# scripted synthetic repairs against real Docker via the No-Key Container Smoke job.
```

The scripted synthetic **2/2** test cases are *not* autonomous-model accuracy,
real admissions quality, or multi-tenant proof. A valid CI needs a check run
for this PR's exact HEAD, not a screenshot of an older green commit.

**Out of scope / unresolved:** OS kernel isolation strength, daemon-side
compromise or Docker escape, rootless/user-namespace enforcement across
deployment hosts, rigorous seccomp profile review, untrusted Docker image
supply chain, real-world privacy/data retention, and model-directed external
provider execution. Docker here is defense in depth around deliberately
constrained AST patches. Do not describe it as an audited production-grade
arbitrary-code-execution SaaS.

**Deployment gate:** no public API exposure of the code executor, no mounted
credentials, no real applicant records, no host Docker socket inside sandbox;
future use of arbitrary model-authored code requires a separate threat model,
rootless/micro-VM isolation evaluation, cross-tenant adversarial tests, runtime
monitoring and human approval for side-effect tools.
