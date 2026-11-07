# Security Policy

## Reporting

Please **do not** open a public issue for a vulnerability. Use GitHub's
private vulnerability reporting ("Security" → "Report a vulnerability") on this
repository. If that is unavailable, contact the maintainer privately first.

Please do not include real personal data, working credentials, or exploit
payloads against any third-party system in a report.

## Accepted risk (closed)

Issue #58 tracks a historical public Git object containing a removed 72 MiB
admissions/user-message database. The current tree is clean, but the object is
still served for a historical commit SHA. Issue #58 was closed on 2026-10-31 as
**CLOSED / NOT_PLANNED / ACCEPTED_RISK** by owner disposition: administrative
risk acceptance, not remediation. History rewrite/force-push remains
intentionally not taken; do not treat the current-tree cleanup or the closure
as a purge, safety verification, or licensing determination.

## Threat model of this demo

This repository is a **demonstration**, not a production service. Its security
posture is scoped accordingly.

Protected:
- Session identity (server-issued ids, HMAC-bound bearer tokens)
- Administrator access: separate HMAC-signed tokens, role hierarchy
  (`viewer < operator < admin`), environment-injected PBKDF2 password hashes,
  optimistic-lock review, and redacted operation audit logs
- Prompt-injection detection on user input
- Output boundary rules (no fabricated data, source marking)
- Absence of real data and credentials

Explicitly **not** claimed:
- No end-user account system; the admin surface is a small, separate RBAC
  layer, not a full IAM/MFA product
- No rate limiting that survives a determined attacker at scale
- No availability or durability guarantees
- No multi-tenant isolation guarantees
- No production clearance: TLS, real-data authorization, credential rotation,
  cross-host HA and capacity/SLO remain open (see the two sections below)

## Running it safely

- Set a strong, random `SESSION_SECRET` for anything beyond a local demo.
  Without it a fresh secret is generated per process, which invalidates tokens
  across restarts and workers.
- `SESSION_SECRET` is a bearer-authority secret. Anyone holding it plus a
  session id can mint that session's token.
- Do not expose this service to the public internet as-is. There is no
  multi-tenant isolation and the dataset is synthetic anyway.
- Generate admin credentials with `python scripts/setup_admin_env.py`; never
  commit `.env.admin.local`. Production must set `ADMIN_TOKEN_SECRET` and must
  not use plaintext `ADMIN_PASSWORD`.
- The in-process login/job limiter is per-worker. A multi-worker deployment
  should move counters to Redis; this is documented rather than claimed.

## Dependency policy

The **runtime** dependency set (`requirements.txt` → `requirements.lock`) must
have **zero known vulnerabilities**. CI enforces this with `pip-audit`; the
job is a hard gate and a red `Security Audit` means the runtime set is not
acceptable to ship.

The **development** dependency set is locked separately in `uv.lock` and pinned
by `[tool.uv] constraint-dependencies` in `pyproject.toml`; the frontend uses
`npm overrides` for the same reason. These floors exist so a plain
re-resolution cannot float a package back into an advisory range. Change them
deliberately, then re-run the audit and the test suite.

Optional tooling that is not part of the runtime lives in its own file
(`requirements-legacy-admin.txt` → `requirements-legacy-admin.lock`), is never
installed by default, never enters the container image and is audited
separately against an explicit, recorded exception list.

The reviewed version pins live in `requirements.lock.constraints`, and the
current state of the policy — including every exception, its rationale and the
open decisions — is documented in
[`docs/dependency-exceptions.md`](docs/dependency-exceptions.md). Regenerate
the lock with `make lock`; do not hand-edit it and do not re-resolve it
without the constraints file.

Report a vulnerable dependency the same way as any other issue: privately, not
in a public issue or pull request.

## Java overlay security posture

The optional Java Gateway + read-only Catalog overlay (merged in PR #89) is
covered by CI fault-injection, but it is still a demo deployment:

- The catalog runs on a dedicated MySQL account (`gaobao_reader`) whose grants
  are limited to `SELECT` on `schools`, `admission_scores` and
  `enrollment_plans`; the session tables, `UPDATE` and `CREATE TABLE` are
  verified to be denied.
- The catalog route is protected by a Redis Lua token bucket that **fails
  closed**: a Redis outage, timeout or script error returns HTTP 503 instead of
  letting traffic through. The bucket is one global key shared by all
  anonymous catalog traffic — it is a capacity guard, not a per-user quota.
- Nginx in the Java overlay strips caller-supplied `X-Forwarded-For` and
  rewrites it from the real peer, so the limiter key cannot be spoofed.
- The overlay only ever runs behind HTTP on port 80. TLS, Redis ACL/TLS, key
  rotation, Java dependency SCA/SBOM and cross-host HA are **not** done.

## Production is not cleared

Zero open Dependabot alerts means no *known* advisory is outstanding for the
tracked lock files. It is not a statement that the system has no security
risk. Before any public deployment the following still need to be closed and
approved separately: TLS/WAF, authorised real data and privacy review,
dedicated credential management and rotation, cross-host HA, capacity and
p95/SLO testing, and a penetration test. Code being merged into `master` is
not approval to serve production traffic.
