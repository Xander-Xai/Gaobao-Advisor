# ADR-010: Chat-submitted, operator-approved admissions research

- **Status:** Accepted
- **Date:** 2026-11-25
- **Context:** Issue #204 asked for Deep Agents research to be reachable from the
  formal consultation experience. The architecture freeze keeps ordinary chat
  deterministic and prevents a user turn from silently triggering model spend,
  private-data access, or sandbox execution.

## Decision

The Chat UI may submit an explicit, structured admissions research request to
`POST /api/v1/chat/research-runs`. The endpoint authenticates the existing
session capability, binds ownership to a one-way session identifier, and writes
the request to the existing private admissions research journal. The server
supplies the configured dataset, model, image, and authorization reference;
the caller cannot choose those privileges. The existing independent operator
approval, worker, Deep Agents engine, SQL evidence checks, sandbox policy,
receipt verifier, cancellation and report lifecycle remain authoritative.

Public `POST /api/v1/chat` uses a narrow deterministic phrase detector for
explicit multi-school / multi-constraint requests. A match suppresses
the consultation graph, SQL/RAG business lookups, free-form model response
generation and the separate paid quality-judge call for that turn, after the
existing security scan. It emits a `research_intent` SSE event with a
conservative instruction. When the non-production request feature is enabled,
the Chat UI opens the structured form. The detector does not extract
school/major scope, create a TaskRun, or authorize a provider call; users must
confirm a bounded scope through the form. Simple factual questions
retain the existing deterministic consultation/SSE path. Submitting a request
does not call a provider; an operator must approve the exact immutable
specification and an authorized worker must separately execute it. Status SSE
reports journal states without fabricated percentages. Reports remain session
owner-bound and contain verified aggregates, not model-authored advice or raw
SQL rows.

The user-request route is disabled by default, returns unavailable in
`APP_ENV=production` and the industrial profile, and requires an explicit
non-production opt-in plus the existing managed-research configuration. It is
not a production, real-data, automatic-authorization, or real-model acceptance
claim. Simple facts remain on the deterministic SQL path. No second Harness,
queue, journal, task state machine, or persistence lifecycle is introduced.

## Consequences

- A Chat user can request a bounded two-school/one-major historical comparison
  and observe the existing TaskRun's real approval/execution/terminal state.
- Operators can discover pending requests in the existing workbench queue.
- Intent detection is deliberately conservative and phrase-based, not an LLM
  classifier. Ambiguous free-text requests are never converted into task
  specifications; the caller must provide the explicit supported scope.
- Real-data authorization, provider budget approval, privacy review, and
  production deployment remain separate release gates.

## Verification

`tests/test_chat_sse.py` covers the deterministic intent boundary and verifies
that a detected complex request bypasses free-form generation and the separate
quality-judge model call. Frontend tests cover attaching the intent event to the
right assistant turn and opening the request form.
`tests/test_chat_research_runs.py` exercises session ownership, idempotency,
disabled-by-default behavior, operator queue visibility, and the
submission-to-approval-to-worker-to-private-report lifecycle against the
existing journal with a deterministic injected controller. Frontend tests
exercise the Chat form, intent-event expansion, and operator queue. These tests
do not call a real model or establish real-data accuracy.
