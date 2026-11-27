# LLM Resource Governance

**Status**: implemented and verified in this repository. The shipped quota
values are a **test configuration**, not a real provider contract. Real
admission numbers, real provider behaviour and production acceptance are
tracked separately in [`docs/PROJECT_STATUS.md`](../PROJECT_STATUS.md).

This document describes the code that ships in this repository. It is not a
statement about a deployed service.

## 1. Why the HTTP rate limit is not enough

The public entry limiter ([`server/middleware/ratelimit.py`](../../server/middleware/ratelimit.py))
bounds *client* requests per IP. One accepted consultation is not one model
request. A single turn of the ordinary consultation path can produce:

| Internal step | Model calls | Where |
|---|---|---|
| History summarisation | 1 | `server/agent/memory_manager.py` |
| Answer draft (non-streaming graph path) | 1 | `server/graph/nodes/llm_node.py` |
| Answer draft (public SSE path) | 1 | `server/graph/nodes/llm_node.py` |
| Quality judge (one call per dimension, in parallel) | up to 5 | `quality/judge/judge_router.py` |
| Rejected-answer repair | 1 | `server/quality/chat_repair.py` |
| Knowledge embedding | 1 per batch of 32 | `server/services/kb_retriever.py` |

Each of those calls could also be retried, and a provider could be swapped for
a fallback provider. Before this change, nothing bounded that internal fan-out:
the only limits were the per-IP HTTP bucket and the administrator limits. A
single client could therefore turn one accepted HTTP request into a burst of
paid provider calls, and a provider quota breach could not be attributed to
anything the service could see.

## 2. Governance model

### 2.1 Two layers

```
        HTTP entry (per client IP)          LLM Resource Governor (per provider)
        ───────────────────────────          ─────────────────────────────────────
        one accepted request        ──▶      many governed model calls
        Redis token bucket                    Redis Lua admission
                                               ├─ RPM   requests / minute
                                               ├─ TPM   tokens / minute
                                               └─ concurrency (lease semaphore)
```

The HTTP limiter stays exactly as it is. The governor is a second, independent
layer that owns the *provider* budget. The two are not alternatives: the HTTP
limiter protects the service, the governor protects the provider contract.

### 2.2 Quota scopes

A call is charged against one or more **quota scopes**:

| Scope | Key | Meaning |
|---|---|---|
| Account scope | `provider:<name>` | The provider (or the pool it belongs to) contract. |
| Pool scope | `pool:<name>` | Several providers that share one API key and one contract. |
| Model scope | `model:<name>` | A model with its own budget, charged in addition to the account scope. |

The precedence is: a model that declares its own budget adds a second scope
that is charged in the same atomic admission; otherwise the provider is
charged, through its pool when it belongs to one; the process defaults fill
the dimensions a provider does not declare.

### 2.3 Provider semantics are not uniform

Real providers do not meter the same way, so the configuration carries the
semantics per provider instead of assuming one shape:

* `rpm_window: sliding | fixed` — a rolling window never allows a boundary
  burst; an aligned window is cheaper and matches providers that meter in
  whole minutes.
* `tpm_counting: total | input | output` — whether a provider counts input
  tokens, output tokens or both against its token budget.
* `lease_ttl_seconds` — how long a concurrency slot may be held before it is
  reclaimed.

The shipped file contains **placeholder test numbers**. They are not a
provider's real quota and must not be presented as one. The acceptance of the
governor against a real provider contract is tracked in
[Issue #216](https://github.com/Xander-Xai/Gaobao-Advisor/issues/216).

## 3. Components

| Component | File | Responsibility |
|---|---|---|
| Governor | [`server/services/llm_governor.py`](../../server/services/llm_governor.py) | Quota resolution, atomic admission, token accounting, lease lifecycle, failure policy. |
| Gateway | [`server/services/llm_gateway.py`](../../server/services/llm_gateway.py) | The only place a provider request may leave the process. Retry, fallback, `Retry-After`, streaming lifecycle, usage reconciliation. |
| Governed chat model | [`server/agent/governed_chat_model.py`](../../server/agent/governed_chat_model.py) | Intercepts the provider call of a LangChain chat model (Deep Agents) without replacing the model. |
| Metrics | [`server/metrics.py`](../../server/metrics.py) | Prometheus counters, gauges and histograms of the governor. |
| Configuration | [`config/llm_governance.yaml`](../../config/llm_governance.yaml) | Provider, pool and model budgets. Test values. |

### 3.1 Atomic admission

Every admission is **one** Redis Lua script
(`server/services/llm_governor.py::_ADMIT_LUA`). It verifies every scope
first and only then consumes the budget, so a partial grant — a token
reservation without a concurrency slot — cannot leak budget.

Redis key layout (one hash tag per scope, so a clustered Redis keeps the
multi-key script valid):

```
{gaobao:llm:v1:provider:deepseek}:rpm          sorted set, sliding request window
{gaobao:llm:v1:provider:deepseek}:rpm:fixed     counter, aligned request window
{gaobao:llm:v1:provider:deepseek}:tpm          sorted set, sliding token window
{gaobao:llm:v1:provider:deepseek}:tpm:sum      running token total of the window
{gaobao:llm:v1:provider:deepseek}:tpm:fixed     counter, aligned token window
{gaobao:llm:v1:provider:deepseek}:conc          sorted set of concurrency leases
```

### 3.2 Concurrency is a lease semaphore, not a token bucket

A token bucket limits the *rate* of admission. It cannot express "at most
five provider calls may be in flight", and it cannot express "the slot is
held until the call finishes". The governor therefore uses a distributed
semaphore with leases:

* an admitted call adds a member to the `conc` sorted set, scored with the
  moment the lease expires;
* the lease is renewed while the call is alive;
* an expired lease is reclaimed by the next admission, so a crashed worker
  cannot hold a slot forever;
* a lease that already expired is never resurrected, so a slow worker cannot
  take back a slot that was given to somebody else.

The renewal is fenced: `ZSCORE` must still be in the future, otherwise the
renewal is refused.

### 3.3 Token accounting

1. **Estimate** the input from the request (`estimate_messages_tokens` for a
   chat request, the character heuristic for an embedding batch).
2. **Reserve** the output from `max_tokens`, or from the configured default
   reservation when the caller does not bound the output.
3. **Charge** the counted amount at admission. Which amount is counted
   depends on the provider's `tpm_counting` mode.
4. **Reconcile** with the provider's real usage (`usage.prompt_tokens` /
   `usage.completion_tokens`) when the call ends. The reservation is replaced
   by the real number, so an over-reservation is refunded and an
   under-reservation is charged.

For a streaming call the provider usually does not report a usage, so the
produced text is measured with the same heuristic and used for the
reconciliation.

### 3.4 Every attempt is admitted

The gateway owns the retry loop, and the SDK's own retry is disabled
(`max_retries=0`). One attempt is one admission:

```
provider chain: [primary] → [fallback …]
  for each provider:
    for each attempt:
      lease  = governor.acquire(...)        # refused attempts never call the provider
      try:   response = provider.create(...)
      except 429/5xx: release lease, back off, retry (a new admission)
      else:   settle lease with the real usage, release lease
```

A retry therefore cannot exceed the budget, and a provider fallback is a new
admission against the fallback provider's own budget — not a way around the
primary provider's budget.

### 3.5 429, Retry-After and degradation

* `Retry-After` is honoured, bounded by `GAOBAO_LLM_GOVERNOR_MAX_RETRY_AFTER`
  (30 s by default).
* `x-ratelimit-reset-requests` and `x-ratelimit-reset-tokens` are parsed as
  well, so providers that publish a reset hint instead of `Retry-After` are
  understood.
* Other retryable statuses (500, 502, 503, 504) use an exponential backoff
  with jitter.
* A provider that stays unavailable degrades to the next provider of the
  configured fallback chain. A provider without a credential is skipped.
* If every provider fails, the caller receives the last real failure, or the
  governor refusal when the budget — not the provider — was the reason.

### 3.6 Streaming lifecycle

The concurrency slot is returned when the stream ends, when it fails, when the
consumer closes it and when the consumer's task is cancelled. The async path
additionally registers a task-completion callback, because a cancelled task
does not close an async generator on its own.

The admission itself is shielded from cancellation: a request that is
cancelled while it waits for admission returns the slot it was granted a
moment earlier instead of leaking it.

### 3.7 Failure policy when Redis is unavailable

The behaviour is explicit and configured, never implicit:

| `GAOBAO_LLM_GOVERNOR_REDIS_FAILURE_MODE` | Behaviour |
|---|---|
| `fail_closed` (default) | The model call is refused. The caller degrades to its deterministic answer. No unbounded call is possible. |
| `local_fallback` | The governor degrades to a bounded in-process governor with the same limits. The budget is no longer shared between instances, but the calls stay bounded. |

The production default is `fail_closed`. Both modes are bounded: there is no
configuration in which a Redis outage produces unlimited provider calls.

## 4. Observability

Prometheus metrics in [`server/metrics.py`](../../server/metrics.py):

| Metric | Type | Meaning |
|---|---|---|
| `llm_governor_requests_total{provider,model,operation}` | counter | Admitted model requests. |
| `llm_governor_rejections_total{provider,reason}` | counter | Refused requests (`rpm`, `tpm`, `concurrency`, `redis_unavailable`). |
| `llm_governor_active_concurrency{provider}` | gauge | In-flight model calls of this process. |
| `llm_governor_tokens_reserved_total{provider,model}` | counter | Tokens reserved at admission. |
| `llm_governor_tokens_used_total{provider,model}` | counter | Tokens actually consumed after the reconciliation. |
| `llm_governor_wait_seconds{provider}` | histogram | Time spent waiting for an admission. |
| `llm_provider_429_total{provider}` | counter | Provider rate-limit responses. |
| `llm_governor_fallback_total{provider}` | counter | Moves to the next provider of the chain. |
| `llm_governor_retries_total{provider,reason}` | counter | Retried calls; every retry is a new admission. |

Logging is deliberately narrow. A governed call logs the provider, the model,
the operation, the token counts and the outcome. It never logs the prompt, the
completion, the user identifier or the API key; the error text of a provider
failure is replaced by the exception type.

Label values are bounded: provider and model identifiers come from the
configuration, and the outcome codes come from a fixed set.

## 5. Model call path coverage matrix

Every path that can reach a provider is listed. "Governed" means the call
passes the governor, including its retry and its fallback.

| # | Path | Module | Provider call | Governed by |
|---|---|---|---|---|
| 1 | Consultation answer, synchronous graph path | `server/graph/nodes/llm_node.py` | `chat.completions.create` | Gateway (`operation=chat`) |
| 2 | Consultation answer, legacy synchronous stream | `server/graph/nodes/llm_node.py` | streaming `chat.completions.create` | Gateway (`operation=chat`) |
| 3 | Consultation answer, public SSE path | `server/graph/nodes/llm_node.py` | streaming `chat.completions.create` | Gateway (`operation=chat`) |
| 4 | Conversation history summary | `server/agent/memory_manager.py` | `chat.completions.create` | Gateway (`operation=summary`) |
| 5 | Rejected-answer repair | `server/quality/chat_repair.py` | `chat.completions.create` | Gateway (`operation=repair`) |
| 6 | Quality judge (one call per dimension) | `quality/judge/judge_router.py` | `chat.completions.create` | Gateway (`operation=judge`) |
| 7 | Knowledge embedding (batch of 32) | `server/services/kb_retriever.py` | `embeddings.create` | Gateway (`operation=embedding`) |
| 8 | Voice rendering | `server/services/voice.py` | `chat.completions.create` | Gateway (`operation=voice`) |
| 9 | Deep Agents research engine | `server/agent/admissions_deepagents.py` | LangChain `ChatOpenAI` | `govern_chat_model` (`operation=research`) |
| 10 | Deep Agents adapter | `server/agent/deepagents_adapter.py` | LangChain `ChatOpenAI` | `govern_chat_model` (`operation=research`) |
| 11 | Sandbox analysis code generation | `server/agent/admissions_analysis_sandbox.py` | `chat.completions.create` | Gateway (`operation=sandbox`) |
| 12 | Native harness planner | `server/agent/native_taskrun_harness.py` | `chat.completions.create` | Gateway (`operation=planner`) |
| 13 | Read-only planner (client injected) | `server/agent/readonly_model_planner.py` | `chat.completions.create` | Gateway, injected by the caller |
| 14 | Decision-card planner (client injected) | `server/agent/decision_card_model_planner.py` | `chat.completions.create` | Gateway, injected by the caller |
| 15 | Replanning policy (client injected) | `server/agent/synthetic_replanning.py` | `chat.completions.create` | Gateway, injected by the caller |
| 16 | Offline benchmarks (model mode) | `benchmarks/readonly_model_eval.py`, `benchmarks/context_ab_eval.py`, `benchmarks/decision_card_business_eval.py`, `benchmarks/synthetic_replanning_eval.py`, `benchmarks/sandbox_coding_agent.py`, `benchmarks/sandbox_model_planner.py` | `chat.completions.create` | Gateway (`operation=planner` / `operation=sandbox`) |
| 17 | Local Ollama embedding | `server/services/kb_retriever.py` | local HTTP, no provider quota | Not applicable — no provider contract |

### Uncovered paths

| Path | Reason |
|---|---|
| `legacy/agent.py` | Deprecated Streamlit mode. It is explicitly marked as deprecated in its own header, it is not imported by `server/main.py`, and it is not started by any deployment entry point. It keeps its own credentials and its own base URL. It must not be started in a deployment that shares the provider budget; if it is ever revived, it has to be routed through the gateway first. |
| `server/services/kb_retriever.py::OllamaEmbedding` | A local model server. There is no provider quota and no paid request. |
| `quality/judge/hallucination.py` | Deterministic rule checks, no model call. |

`tests/test_llm_governance_coverage.py` scans the repository for a new direct
provider call and fails when one appears outside the gateway.

## 6. Configuration

`config/llm_governance.yaml` — **test values**, see the header of the file.

Process level switches (environment):

| Variable | Default | Meaning |
|---|---|---|
| `GAOBAO_LLM_GOVERNOR_ENABLED` | `true` | Turns the governor off. An explicit opt-out, not a default. |
| `GAOBAO_LLM_GOVERNANCE_CONFIG` | `config/llm_governance.yaml` | Path of the quota file. |
| `GAOBAO_LLM_GOVERNOR_NAMESPACE` | `gaobao:llm:v1` | Redis key prefix. Use a distinct prefix per environment. |
| `GAOBAO_LLM_GOVERNOR_REDIS_FAILURE_MODE` | `fail_closed` | `fail_closed` or `local_fallback`. |
| `GAOBAO_LLM_GOVERNOR_MAX_WAIT_SECONDS` | `20` | How long a call may wait for a quota before it is refused. |
| `GAOBAO_LLM_GOVERNOR_LEASE_TTL_SECONDS` | `120` | Default lifetime of a concurrency lease. |
| `GAOBAO_LLM_GOVERNOR_OUTPUT_RESERVATION` | `512` | Output tokens reserved when the caller does not pass `max_tokens`. |
| `GAOBAO_LLM_GOVERNOR_MAX_ATTEMPTS` | `2` | Attempts per provider before the fallback is used. |
| `GAOBAO_LLM_GOVERNOR_MAX_RETRY_AFTER` | `30` | Upper bound for the provider's own backoff hint. |
| `GAOBAO_LLM_GOVERNOR_RETRY_BASE_DELAY` | `1.0` | Base of the exponential backoff, in seconds. |
| `GAOBAO_LLM_RESEARCH_PROVIDER` | `openai` | Governor provider name used by the research worker and the sandbox. |

The provider endpoint and credential keep their own namespace
(`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, and the
per-provider `LLM_API_KEY_<PROVIDER>` / `LLM_MODEL_<PROVIDER>` /
`LLM_BASE_URL_<PROVIDER>`). The research worker, the sandbox and the
benchmarks keep `OPENAI_API_KEY` / `OPENAI_API_BASE`; they pass them to the
gateway as an explicit provider configuration, so they are governed without
renaming their credentials.

## 7. Verification

```bash
# unit tests: admission, accounting, retry, fallback, streaming, failure policy
python -m pytest tests/test_llm_governor.py -q --no-header --no-cov

# repository scan: no provider call outside the gateway
python -m pytest tests/test_llm_governance_coverage.py -q --no-header --no-cov

# integration tests against a real Redis 7
REDIS_TEST_URL='redis://127.0.0.1:6380/0' \
  python -m pytest tests/test_llm_governor_redis.py -q --no-header --no-cov
```

The Redis tests create a random key namespace and delete only the keys they
own. They never flush the database and never contact a paid provider: the
provider is a mock.

## 8. Known limits

* The shipped quota values are placeholders. Until an operator fills in the
  real contract, the governor enforces a *bounded* budget, not the provider's
  actual quota.
* The sliding token window keeps one sorted-set member per request. At a very
  high request rate the member count grows with the request rate inside one
  minute; the aligned window mode (`tpm_window: fixed`) avoids that at the
  price of a boundary burst.
* The in-process concurrency gauge is best effort. The authoritative
  concurrency is the Redis lease set, which is shared.
* The governor bounds the *provider* budget. It does not bound what one
  request may cost in total; that is the task-level budget's responsibility.
* A provider that does not report its usage cannot be reconciled exactly. The
  produced text is measured with the same heuristic instead, which is an
  estimate and is labelled as one in the metrics.
