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
docker build -f Dockerfile.sandbox -t gb-advisor:sandbox-ci .
IMAGE_ID=$(docker image inspect --format '{{.Id}}' gb-advisor:sandbox-ci)
python -m benchmarks.sandbox_workspace --image "$IMAGE_ID" --out /tmp/gaobao-sandbox.json --failure-probes
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

### Real-Docker negative acceptance

The optional `--failure-probes` switch is required by the existing No-Key Container Smoke Test. It checks a nonzero exit despite a valid marker, a zero exit with only a substring marker, a bounded output flood, and an execution timeout. Each must fail with its specific expected result: a missing daemon/image cannot pass the negative suite. Only booleans are added to the sandbox receipt; no raw output. The trusted fixed oracles are not model-controlled. Each real probe also attempts exact-name removal and requires a successful empty daemon listing for that name; daemon failure or a remaining container cannot pass. This is a point-in-time cleanup check, not a guarantee against daemon races, host compromise or multi-tenant escape. Review the actual CI result before claiming execution.

## Business extension: isolated admissions data analysis (2026-11-25)

The earlier synthetic coding exercises remain security regressions, not the
business use of the sandbox. The live data explorer and advisor graph use
trusted SQL via `server/services/data_query.py` and `server/services/data_catalog.py`.
Do not sandbox every ordinary lookup or let model code access that database.

The new operator-local tool `server/agent/admissions_analysis_sandbox.py` can
analyze an authorized private admissions database (SQLite/MySQL). The host
selects only numeric AdmissionScore columns for a fixed province, subject,
batch, year range, and school/major level, with an optional school ID.
The agent-generated Python file receives only this bounded snapshot and
grouping plan through a read-only mount. No database URL, user profile,
chat history, secrets, network, Docker socket or host writable mount enters
the container. The immutable image runs nonroot with CPU, memory, process,
wall-clock and stdout limits; cleanup is checked before accepting results.

Example: compare historical minimum-score medians and ranks for Guangdong
physics, undergraduate batch, 2022–2025, grouped by year. Set DATABASE_URL
to a locally authorized, preferably read-only database account. Generate
an analysis program outside the sandbox; it reads /input/rows.json and
prints one JSON object with groups sorted by key. Each group must contain
key (array of grouping IDs), records, missing_min_scores, median_min_score,
lowest_min_score, highest_min_score, best_min_rank, worst_min_rank (null when
unavailable). The host independently recomputes these metrics; only an exact
match returns VERIFIED_DESCRIPTIVE_ANALYSIS. This is not an admission
probability, verified source provenance, or a recommendation.

```bash
docker build -f Dockerfile.sandbox -t gaobao-analysis-local .
IMAGE=$(docker image inspect --format '{{.Id}}' gaobao-analysis-local)
python -m server.agent.admissions_analysis_sandbox --authorized-data \
  --approval-ref internal-data-approval-id --province 广东 --subject-type 物理 \
  --batch 本科批 --year-from 2022 --year-to 2025 --group-by year \
  --code /private/analysis.py --image "$IMAGE" --out /private/analysis-receipt.json
```

The operator approval flag is an explicit attestation, not automatic proof
of licensing or lineage. Run only on authorized non-personal admissions
records. Receipts contain scoped aggregate values and hashes, not raw
records or stdout. Keep receipts private. No silent fallback or writes
to the admissions tables. CI uses a fictional SQLite fixture through the
same SQL and OCI path; real authorized dataset use is pending.

This business slice is callable from an operator-side Agent tool host or CLI,
not registered in anonymous chat or the synthetic-only Harness. Model mode
replaces `--code /private/analysis.py` with `--model <provider-model>
--authorize-model-call` and uses `OPENAI_API_KEY` / optional `OPENAI_BASE_URL`.
The bounded Agent loop generates code, executes it in Docker, independently
verifies output, and repairs once on a recoverable program/oracle failure.
It sends only the schema, grouping fields and fixed failure code to the model,
never SQL rows, profiles or approval reference. At most two provider requests,
1800 completion tokens per request, no SDK retries. Provider-reported usage is
recorded, but costs and model effectiveness remain unmeasured.
The next slice is authenticated task dispatch, owner-bound receipts, review
and report attachment via a separate sandbox worker (no Docker socket in
FastAPI); arbitrary user code and production multi-tenancy remain out of scope.
