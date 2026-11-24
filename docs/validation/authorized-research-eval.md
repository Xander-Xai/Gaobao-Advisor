# Authorized real-data research evaluation

The existing synthetic benchmark remains useful for regression, but cannot grade actual admissions research. The new offline evaluator consumes private receipts from the existing Deep Agents + Sandbox business entrypoint and a separately reviewed gold manifest. No model requests are made by this evaluator. Prepare the actual independent source labels first; do not generate the gold hashes from the candidate receipts. Use at least four unique cohorts, with complete and incomplete/negative cases in both development and holdout. Each case freezes scope, expected status, coverage, canonical evidence and analysis hashes (null for expected blocked cases), and an independent source reference. Manifest schema: authorized-admissions-research-gold/v1, dataset_ref, review_ref, cases; see validate_manifest. The private receipts file is an object mapping each case ID to its full admissions-research/v1 receipt. Keep both inputs outside Git, use authorized sources and a reviewer independent of candidate execution. Run:

```bash
python -m benchmarks.authorized_research_eval --gold /private/gold.json --receipts /private/receipts.json --out /private/eval-new.json --authorize-private-eval
```

The evaluator rejects missing cases, reused run IDs, duplicate cohorts (including reversed schools), scope drift, wrong coverage, self-consistent but wrong aggregate and unsupported admission prediction claims. The report contains only case IDs, outcomes, reasons, review/data digests and provider-declared usage (unknown remains null). Source licensing/provenance, billing, actual model transport and statistical validity remain independently unverified. REVIEW_REQUIRED is not approval. Tests use fixtures; real source review and authorized real provider runs are still required. No automatic optimizer or production release.


## Managed TaskRun aggregate evaluation

For the managed operator workflow, do not rerun a paid model or export full SQL
receipts just to evaluate. Freeze an independently reviewed gold manifest and a
private JSON object mapping each case ID to one distinct managed research run ID.
Use the same evaluator with the existing private journal:

```bash
python -m benchmarks.authorized_research_eval --gold /private/gold.json \
  --managed-runs /private/case-run-map.json --journal /private/research.sqlite \
  --expected-model openai:APPROVED_MODEL --expected-image sha256:APPROVED_DIGEST \
  --out /private/managed-eval-new.json --authorize-private-eval
```

The local authorized operator reads one SQLite snapshot in read-only mode;
the evaluator verifies immutable spec digests, scope, dataset/model/image,
independent approval, terminal state, stored evidence digest, coverage and
aggregate analysis against the gold. No raw SQL rows or approval references
are exported and no task is created, expired, requeued or executed. The
journal is a trusted local input, not a cryptographic attestation of provider
execution. Unlike the full receipt lane, the aggregate lane cannot recompute
the raw evidence digest or independently verify provider transport. An
expected BLOCKED case cannot pass on a generic FAILED or cancelled run:
a generic failure projection does not distinguish correct business rejection
from infrastructure failure. Only the explicit empty-history reason described below
may be scored with an independent reason label. The full receipt lane applies the same conservative rule: BLOCKED without an independently labelled and verified rejection reason is not a passing task. A provider outage, unverified finish or changed source can withhold a report safely without completing the expected business task. Use VERIFIED_PARTIAL_RESEARCH with independently frozen coverage for incomplete-data cases; keep true business-rejection acceptance open pending a reason oracle. Neither lane authorizes production release or establishes admission prediction accuracy.

## Delegation receipt and coverage integrity

The managed worker must verify the research schema, SDK engine marker, inner task ID and both delegation receipt digests against the exact returned evidence and approved scope before publishing an aggregate. The full-receipt evaluator recomputes the same task/scope/dimension/rows binding. Empty, substituted or stale receipts are not valid completion evidence. The managed aggregate evaluator still cannot recompute raw rows from a journal alone. Gold coverage coordinates must belong to the approved schools and years, and each coordinate can occur only once per dimension across missing, ambiguous and incomplete. These are structural checks, not an independent data-source or model-transport attestation. Existing stored aggregate records are not retroactively certified; rerun authorized verification before relying on older receipts. Business-rejection reason labels remain an independent open requirement.


## Narrow deterministic refusal oracle (managed lane only)

An approved cohort with no historical admission rows cannot enter the descriptive sandbox. The actual research runner now checks that condition before model construction. The managed worker independently re-reads the exact approved SQL scope before recording `FAILED` with `failure_code=no_admission_history` and no report. The independent gold author may add `expected_block_reason: no_admission_history` only to an expected BLOCKED case; the managed evaluator credits that precise, locally trusted journal observation only. A legacy unlabeled BLOCKED case, generic runtime failure, source change, cancellation or unexpected nonempty recheck never earns credit. Full-receipt mode remains conservative because it lacks the worker's second SQL observation. This is one narrowly defined business rejection; independent source labels and real-data validation remain required. All other business rejection reasons remain open.
