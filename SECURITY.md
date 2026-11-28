# Security policy

## Reporting a vulnerability

Please do not disclose suspected vulnerabilities in public Issues, pull
requests, discussions or screenshots. Use GitHub's private vulnerability
reporting feature from the repository's **Security → Report a vulnerability**
page. If private reporting is unavailable, contact the repository maintainer
privately through a GitHub channel before sharing technical details. This
repository does not publish a dedicated security-team email or response-time
commitment.

Do not include real student records, session tokens, API keys or data from
third-party systems in a report. Share only the minimum sanitized evidence
needed to reproduce the issue.

## Supported scope

The maintained target is the current default branch (`master`). There is no
published release-support matrix or guaranteed security-fix SLA. Check the
live Releases page before assuming that a tag receives ongoing fixes.

## Security boundaries

Gaobao Advisor is an open-source application example. Automated checks and
implemented controls do not certify the system or authorize public production
use. The current architecture includes:

- server-issued anonymous session IDs and HMAC bearer capabilities;
- separate operator/admin authentication and role checks;
- source and output validation on consultation responses;
- session-owned, private research requests with immutable scopes, independent
  operator approval and a single dedicated Worker path;
- allowlisted SQL tools and a restricted Docker analysis boundary for approved
  research; no arbitrary host shell or Docker socket is exposed to public Chat;
- dependency, secret, redistribution, test and CodeQL gates in GitHub Actions.

These controls do not provide a full account/IAM system, multi-tenant
production isolation, provider-side data protection, production TLS, guaranteed
availability, or independently measured admission accuracy. The Sandbox is
bounded to the implemented workload and test matrix; it is not a general-purpose
OS sandbox certification.

## Handling credentials and data

- Keep model API keys and session/admin secrets in local secret stores or
  permission-restricted environment files; never commit them or place them in
  images, build contexts, URLs, browser code, logs or artifacts.
- Do not enable external model calls without explicit authorization, an
  operator-controlled credential and an understood provider data/cost policy.
- Treat user input, model/tool output, imported data and persistent state as
  untrusted. Enforce permissions, scope, budgets and execution boundaries
  outside model prompts.
- Demo datasets and CI research fixtures are synthetic. Do not use them as
  evidence for a real student recommendation.
- The private research Worker can send approved research content to the
  configured external model provider. Do not enable it for personal or
  restricted data until the operator has established authorization and
  provider/privacy controls.

## Historical repository exposure

Issue [#58](https://github.com/Xander-Xai/Gaobao-Advisor/issues/58) documents a
historical public Git object containing a removed database. The owner recorded
an accepted-risk disposition; it did not establish redistribution rights for
the third-party data. In a live check on 2026-11-28, the previously documented
commit/blob API paths and raw-content URL returned 404, and the current default
branch and observed remote branches did not contain the removed database path.
This is evidence about the checked GitHub refs and URLs only, not proof that all
external caches or backups were purged. Keep any retained local forensic copy
private and do not push branches or tags that contain the old history.

## CI and repository settings

The default branch has required status checks, required conversation
resolution, enforced protection and force-push/deletion protections. Private
vulnerability reporting, secret scanning and push protection are enabled.
GitHub Actions workflow permissions default to read-only; individual workflows
request additional permissions only where required. These settings are observed
repository state and may change; check GitHub directly before relying on them.
Passing scans only cover their configured rules and checked commits.

Runtime and optional dependency audit policy is documented in
[`docs/dependency-exceptions.md`](docs/dependency-exceptions.md). Report
security gaps privately rather than adding exploit details to that public log.
