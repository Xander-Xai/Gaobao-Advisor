# Repeated real-model A/B budget contract (2026-11-24)

Later-employer Gaobao Harness/Eval portfolio increment. No provider calls or measured outcomes are claimed.

The repeated model experiment now exposes per-arm request caps and provider-reported token stop thresholds in the no-network plan, and forwards exactly those settings to every child. New executed runs require matching reconciled provider budget receipts before launching another arm. Offline historical comparisons remain readable but cannot mix legacy and new budget receipts across trials.

```bash
python -m benchmarks.repeated_model_experiment --model YOUR_VERSIONED_MODEL \
  --out-dir /tmp/gaobao-eval-new --trials 2 \
  --max-provider-calls-per-arm 64 --stop-after-reported-tokens-per-arm 120000
```

The example is a dry run. Add --execute --permit-external-model --confirm-provider-charges only with an approved key, reviewed provider and budget. Token stop thresholds are checked after responses, not hard token or monetary caps; the final request can overshoot. The sum is a planning value, not a cost guarantee. Complete paired synthetic reports still need independent billing and human review. No real admissions data or production automatic rollout.

```bash
pytest -q tests/test_repeated_model_experiment.py tests/test_model_run_comparison.py
```

After each completed paid arm, the runner also checks the frozen dataset/tool/temperature contract, case partitions, unique run identity and stable, distinct prompt digests against all preceding arms. A mismatch stops further provider spending before the next trial; no incomplete summary is published. Negative controls use stub responses only.
