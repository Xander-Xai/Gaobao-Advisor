# SQL/RAG parallelization decision gate (2026-11-24)

**Production decision: HOLD.** Existing main chat flow remains sequential.
Nothing in this experiment changes LangGraph's execution edges, user answers,
checkpointing, caching or concurrency model.

## What is measured

A no-provider, paired **local microbenchmark** executes the actual existing
`data_query_node` and `rag_retrieve_node` against a newly created, completely
synthetic temporary SQLite database and bundled synthetic RAG corpus. It runs
the two nodes both sequentially and via two read-only worker threads, with
alternating order to reduce cache warm-up bias.

```bash
python -m benchmarks.sql_rag_pair_eval \
  --repeat 12 --warmup 2 --out /tmp/gaobao-sql-rag-paired.json
```

The process refuses `APP_ENV=production` and refuses to run if the application
database engine has already been loaded. It overwrites `DATABASE_URL` **before
the first database import**, creates a new temporary database, seeds only
fictional synthetic schools and majors and deletes the temporary directory
on exit. This must not be run by importing its internal probe into a live
server process.

The JSON contract records per-run milliseconds, P50/P95 for each strategy,
the observed local ratio, SQL error status and exact normalized query/retrieval
output equivalence. Any mismatch or failed SQL invalidates the experiment.

## Limits of the evidence

- A tiny synthetic database has different index, cache, CPU, concurrent
  workload and read-amplification behavior from a real deployment.
- The number of iterations is too small for a statistically justified rollout;
  `significance=NOT_ESTABLISHED`.
- Parallel result equivalence for one synthetic case is **not** equivalent to
  policy/answer correctness; `real_admissions_quality=NOT_MEASURED`.
- The benchmark exercises only the two read-only nodes; it does not measure
  the real end-to-end response time, async SSE first-byte time, or model call
  latency. `real_concurrent_load_gain=NOT_MEASURED`.
- A result where local parallel P95 is lower does **not** authorize replacing
  the sequential production state graph; the JSON always sets
  `production_parallelization_authorized=false`.

## Before a production change

1. Define representative SQL and RAG question mixes and a fixed, licensed
   non-sensitive dataset with independent correctness oracles.
2. Compare under real simultaneous users, fixed cache policy, bounded pool,
   thread safety, DB connection pool, cancellation and failure injection.
3. Observe P50/P95/P99, throughput, error rate, resource use and evidence
   parity, and repeat several times with predeclared decision criteria.
4. Only after a verified quality-neutral improvement, propose an independent
   gated PR for parallel edges. Never convert to multi-Agent just for optics.

This follows the same **measure first, modify second** rule as the existing
real-model eval gate. Real provider A/B remains opt-in and unmeasured here.
