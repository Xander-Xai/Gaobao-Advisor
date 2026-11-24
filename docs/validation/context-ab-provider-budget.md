# Context A/B provider budget and receipt acceptance

Refs #147/#104. Reuse the existing ProviderBudget at the actual planner client,
not a separate budget framework. Paid arms require existing explicit consent,
an isolated environment and a dedicated limited credential. Default dry run:

```bash
python -m benchmarks.context_ab_eval --model YOUR_VERSIONED_MODEL \
  --trials 2 --max-provider-calls-per-arm 64 \
  --stop-after-reported-tokens-per-arm 120000
```

For real execution add the existing three consent switches and a fresh private
output directory. The numbers are example policy settings, not permission.
The request cap is per arm (SDK retries disabled); the reported-token
threshold is checked after each response, NOT a hard token or cost ceiling.
Unknown usage, provider failure, threshold/cap stop, incomplete traces, or
usage reconciliation failure produce a retained incomplete receipt and stop
the parent before the next arm. Conservatively reject even a complete arm
that reaches a threshold. No comparison summary for incomplete arms.
A killed process or failure outside the scorer may leave no receipt; never
interpret that as zero spending. Completed arms must have matching budget
settings, counts, model, dataset, tool and prompt contracts across trials.
Scripted results remain unmetered and ineligible as provider evidence.
This is synthetic SQL evidence compaction, not production multi-turn memory,
real admissions quality or invoice verification.
