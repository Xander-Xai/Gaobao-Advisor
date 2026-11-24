# Chat quality / human triage follow-up (2026-11-24)

Scope: public SYNTHETIC_DEMO consultation path, after merged PR #139. This is
not an admissions deployment, actual applicant adviser, or real-model success claim.

## Answer-state contract

1. The preparation-only LangGraph does not persist a template.
2. When a configured model is used, the async caller buffers an untrusted draft.
3. Deterministic provenance and anti-pattern checks run against that draft.
   These checks now also reject numeric values absent from the current input,
   stored slots, read-only SQL results, or RAG snippets. **A number occurring
   anywhere in evidence does not prove its attachment to a named school.**
4. Optional retry: set `CHAT_REWRITE_ENABLED=true` and configure a permitted
   LLM provider to make **at most one corrective LLM request** for deterministic
   failures. The corrected draft is subject to the same checks. The feature is
   off by default to prevent unapproved usage and model billing.
5. If post-check fails, the LLM Judge explicitly fails/reports hallucination, or
   the Judge cannot score, the model draft is rejected. Only a conservative
   synthetic template/explicit policy refusal is delivered.
6. Any nonpassing model candidate triggers `review_required` with stable
   reason codes, a random review ticket ID and an SSE `review` event. The final
   delivered answer and optional final-score record are stored atomically.
   Rejected model drafts are neither delivered nor saved as final answers.

### Reviewer / HITL boundaries

- `GET /api/v1/admin/chat-reviews`: operator or admin, returns only review
  ticket IDs, anonymous reason codes and SHA-256 digest of the **delivered**
  answer. Does **not** expose session IDs, questions, user profile, prompt,
  tokens, hidden draft, or any external API credentials.
- `POST /api/v1/admin/chat-reviews/{ticket_id}/triage`: admin-only, appends an
  audit event (`acknowledged` or `escalated`) with an external review
  reference. This is a workflow intake/triage action, **not** an expert's
  verification of the factual accuracy of the original answer. Neither
  action promotes a rejected candidate or changes the user-visible answer.
- Tickets and review actions reuse the pre-existing
  `operation_audit_logs` SQL table and only hold privacy-minimized metadata.
  No new schema table or schema migration is required.
- Full authorized expert review of specific school/cohort facts requires a
  separate privacy/consent model, official evidence, human grading rubric and
  tightly permissioned workflow. It remains unimplemented and must not be
  presented as complete.

### Failure and cost limits

- There is no hidden model retry for the optional rewrite
  (`max_retries=0`, one `chat.completions.create` request), and no model
  call for correction with the feature flag off or without a provider key.
- The direct SSE stream is intentionally buffered until validation + final
  persistence. This increases time to first response chunk but prevents
  partial unsafe drafts from reaching clients.
- When any DB write fails, the API emits `FINALIZATION_FAILED` and does not
  issue a `done` success event.
- Judge-unavailable is `unscored`, never the 50-point placeholder treated as
  evidence of factual quality. Only graded final results are written to
  `quality_scores`, in the same transaction as the final answer.

## Offline evidence

```bash
python -m benchmarks.chat_gate_eval --out /tmp/chat-gate-synthetic.json
pytest -q tests/test_chat_gate_eval.py tests/test_chat_final_answer_integrity.py \
  tests/test_chat_review_queue.py --no-cov --override-ini addopts=''
```

The frozen chat-gate negative controls explicitly record one **known false
acceptance**: a number from school A's SQL row can be falsely attributed to
school B in model prose. There is no claim-level NLI/SQL oracle in this
consultation path. The report therefore includes
`real_model_task_success=NOT_MEASURED`, `provider_cost=null`,
`admissions_accuracy=NOT_MEASURED`, `human_reviewed_ground_truth=false`
and `production_eligible=false`.

## Real-model comparison, separate authorized activity

There is an existing opt-in fixed business dataset and comparator:

- `benchmarks.decision_card_business_eval`: 8 independent synthetic SQL
  cases (4 development, 4 holdout); fixed profile and source receipt rules.
- `benchmarks.model_run_comparison`: strict paired report checks for model,
  tool schema, temperatures and dataset identity; does **not** certify provider
  bills by itself.

A true A/B requires user-authorized API key, capped cost, real provider usage
and provenance receipts, repeated runs, independent human-reviewed expected
results and no changed holdout. Do not run paid model calls from public CI or
infer a cost advantage from scripted controllers.

## SQL + RAG parallelization decision

The production graph still runs structured SQL and RAG sequentially. There
are no paired measured end-to-end gains for the current deployment, and cache
or database concurrency may change query behavior. Do not rewire parallel
nodes solely to present a fashionable architecture. First record paired
serial/parallel synthetic-workload latency **and** verify identical results;
then repeat under realistic concurrency with a quality/risk guard before
considering a production change.
