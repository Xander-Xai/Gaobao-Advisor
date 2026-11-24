# Local Agent portfolio profile (2026-11-24)

Default `docker compose up` enables the existing synthetic operator experiments and a separate persistent TaskRun journal. Configure admin credentials as documented in `.env.example` before login; anonymous users do not gain operator access. The raw no-env FastAPI runtime remains opt-in. Production is blocked by the existing environment gate. This is not the default public chat model Harness (PR #172 remains separate).

For a private, fictional-data-only local instance, additionally set `APP_ENV=development`, `USER_MEMORY_ENABLED=true`, `GAOBAO_CHAT_COMPACTION_ENABLED=true`, `GAOBAO_CHAT_SNAPSHOT_ENABLED=true`. Never use these flags for real personal data; snapshots are plaintext and real identity/consent/encryption gates remain unresolved. Real model compaction requires reviewed provider and budget. No automatic user-memory capture. Sandbox remains a CLI-only isolated experiment, not an arbitrary execution API.

Acceptance: start local Compose after configuring operator/admin authentication; verify unauthenticated requests fail and operator example endpoint succeeds; create a synthetic task and approve with a different authorized admin; verify production rejects experiments and explicit false disables them. Run existing API/TaskRun/memory tests and exact-head CI. No new runtime results are claimed.
