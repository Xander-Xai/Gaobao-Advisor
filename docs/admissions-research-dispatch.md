# Approved business research dispatch

Depends on #184. The operator may now schedule a bounded one-shot dispatcher rather than manually supplying each run ID. It selects only already independently approved immutable requests matching an explicitly configured dataset, model and image. The existing atomic claim still arbitrates races. A launch attempts at most three tasks (each retains its existing model and sandbox budget); failures and lease loss do not expand that budget. Expired runs commit EXPIRED and are never automatically replayed. No browser or public chat can trigger paid execution. Example, after private operator configuration and approval:

```bash
python -m server.agent.admissions_research_dispatch --model openai:APPROVED_MODEL --image sha256:APPROVED_DIGEST --max-tasks 1 --authorize-model-call
```

The operator can invoke it from a trusted scheduler with concurrency limits; no cron or paid call is installed by this PR. A global distributed worker quota, automated chat routing, production data and provider execution remain unverified. Tests run against private SQLite fixtures with a stub executor. Parent PR #184 must be integrated before this branch is promoted to master.
