# Approved business research dispatch

Depends on #184. The operator may now schedule a bounded one-shot dispatcher rather than manually supplying each run ID. It selects only already independently approved immutable requests matching an explicitly configured dataset, model and image. The existing atomic claim still arbitrates races. A launch attempts at most three tasks (each retains its existing model and sandbox budget); failures and lease loss do not expand that budget. Expired runs commit EXPIRED and are never automatically replayed. No browser or public chat can trigger paid execution. Example, after private operator configuration and approval:

```bash
python -m server.agent.admissions_research_dispatch --model openai:APPROVED_MODEL --image sha256:APPROVED_DIGEST --max-tasks 1 --authorize-model-call
```

The operator can invoke it from a trusted scheduler with concurrency limits; no cron or paid call is installed by this PR. A global distributed worker quota, automated chat routing, production data and provider execution remain unverified. Tests run against private SQLite fixtures with a stub executor. Parent PR #184 must be integrated before this branch is promoted to master.

Single-host worker concurrency: both manual and scheduled paid execution use an
exclusive nonblocking POSIX file lock for the same private journal. A concurrent
worker cannot claim a task or call the provider. Cancellation or expiry does not
release the lock while the local runner is still executing. The lock file must
remain on a local filesystem, in the existing private journal directory; do not
delete or replace it while workers may be alive. This is one-journal/one-host
serialization, not a distributed quota or a guarantee about a remote provider
still processing requests after worker death.
