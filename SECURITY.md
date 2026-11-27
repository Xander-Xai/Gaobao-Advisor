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
- a disabled-by-default cross-task preference vault that binds a bearer
  credential to a pseudonymous subject, encrypts values at rest, records
  consent and mutation events in a metadata-only audit trail, and refuses
  sensitive personal data and instruction-like text at write time;
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
- Cross-task user memory is a pseudonymous, opt-in feature and is refused
  outside an explicit development/test/local environment. Its subject binding
  is not an identity system: keep it disabled for real users until a verified
  identity source, an operator retention and backup-deletion schedule, and a
  privacy review are in place. See
  [PRIVACY.md](PRIVACY.md) and
  [the memory governance architecture](docs/architecture/memory-production-governance-20261127.md).

## Historical repository exposure

Issue [#58](https://github.com/Xander-Xai/Gaobao-Advisor/issues/58) documents a
historical public Git object containing a removed database. The owner recorded
an accepted-risk disposition and intentionally did not rewrite public history.
That disposition is not remediation or a purge. Do not treat the current tree's
redistribution gate as proof that historical Git objects have been erased.

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
