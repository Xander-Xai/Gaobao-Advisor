# Offline model experiment to optimization review

The existing repeated-model runner emits separate raw, redacted trial reports.
This utility joins exactly 2–5 completed pairs, validates them using the same
review function as the Admin API, and prints the review summary. It does not
call a model, submit to an API, approve, modify prompts, or deploy.

```bash
python -m benchmarks.prepare_optimization_review --run-dir /tmp/gaobao-eval-new --trials 2
```

To prepare the exact Admin API request, optionally add
`--submission-out /tmp/new-private-proposal.json`. The output is exclusive
create (no overwrite), mode 0600 on POSIX. Keep it private, review all report
fields before submission, and do not include real personal data or credentials.
The raw reports are self-declared; the tool cannot attest provider provenance.
An authenticated administrator can separately submit the file to the existing
`POST /api/v1/admin/agent-optimizer/proposals`; the server recomputes the
review, and its maker/checker and rollback simulation gates still apply.

Incomplete trials, unexpected trial files, duplicate JSON fields and oversized
inputs fail closed. The holdout gate is never overridden by this CLI. Real
model execution and production improvement are not claimed.

```bash
pytest -q tests/test_prepare_optimization_review.py tests/test_synthetic_optimization_review.py
```
