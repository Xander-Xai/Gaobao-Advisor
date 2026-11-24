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
the current managed failure projection does not distinguish correct business
rejection from infrastructure failure. Keep this evidence gap open until
independent classification is available. Neither lane authorizes production
release or establishes admission prediction accuracy.
