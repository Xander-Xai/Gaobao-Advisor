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

This is an explicit form in the Chat experience, not automatic
natural-language complexity routing. Public `POST /api/v1/chat` continues its
deterministic consultation/SSE contract and never starts research. Submitting a
request does not call a provider; an operator must approve the exact immutable
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
- Ambiguous free-text requests are not converted to task specifications; the
  caller must provide the explicit supported scope.
- Real-data authorization, provider budget approval, privacy review, and
  production deployment remain separate release gates.

## Verification

`tests/test_chat_research_runs.py` exercises session ownership, idempotency,
disabled-by-default behavior, operator queue visibility, and terminal SSE using
the existing journal. Frontend tests exercise the Chat form and operator queue.
These tests do not call a real model or establish real-data accuracy.
