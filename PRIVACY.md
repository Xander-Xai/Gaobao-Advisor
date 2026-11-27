# Privacy and data handling

This document describes repository behavior and configuration boundaries. It
is not a legal privacy notice, data-processing agreement or compliance
certification. Anyone deploying the software for real users must provide their
own notice, lawful basis, retention schedule and operational controls.

## Data processed by the application

Depending on enabled features and the deployment database, the application may
store:

- synthetic or operator-supplied school, major, admissions, enrollment-plan
  and source-manifest data;
- anonymous conversation messages, extracted profile slots and query state;
- admin audit and data-quality job records;
- generated reports associated with an authorized session;
- versioned conversation-compression snapshots;
- optional, explicitly created cross-task preference entries;
- private research TaskRun metadata and bounded aggregate reports.

The standard local Compose setup is synthetic and local to the deployment. A
database or report path being gitignored does not make its contents encrypted,
automatically deleted or safe to expose.

## Sessions, access and deletion

Anonymous Chat sessions use a server-issued ID and an HMAC bearer token scoped
to that session. This is a capability, not a verified account or real-world
identity. Deployment operators are responsible for protecting signing secrets,
database access, backups and transport.

`DELETE /api/v1/profile/{session_id}/data` removes eligible live session
records, including the derived session snapshots. `DELETE /api/v1/memory/vault`
revokes and removes the separately opted-in cross-task preference vault; an
operator can also erase every vault bound to one subject, which is the
account-deletion equivalent while the project has no account system. Session
deletion does not guarantee deletion from database backups, filesystem
snapshots, logs, in-flight model requests or external providers, and it does
not delete the independent memory vault. Operators must set retention and
backup expiry rules appropriate to their deployment.

## Context snapshots and long-term preferences

The standard local synthetic profile can enable bounded conversation
compression and AES-GCM-protected snapshot rows. Snapshots are derived context,
not authoritative admissions evidence; original dialogue is still canonical
conversation state. Key loss/rotation can invalidate snapshots. Snapshot
encryption does not encrypt every conversation record, database backup, log or
provider request.

Cross-task preference memory is separate, explicit opt-in and disabled by
default. Since 2026-11-27 a vault is bound to a pseudonymous *subject* id as
well as its bearer credential: both must be presented, and only a server-side
HMAC fingerprint of the subject is stored. The subject is not a verified
real-world identity. Preference values are stored as AES-GCM ciphertext bound to
the vault and memory key, with a rotatable key ring
(`USER_MEMORY_ENCRYPTION_KEY` plus read-only retired keys); entries written
before encryption was introduced stay readable as `LEGACY_PLAINTEXT` until an
operator re-keys them.

Vault creation requires an explicit consent flag, and consent, mutation,
rotation and revocation events are recorded in a metadata-only audit trail that
never contains a preference value, a credential or a subject. A field
allowlist with data classes and a sensitive-value screen refuse exam scores,
identity documents, contact details, family context, external links and
instruction-like text; conversation content is never promoted into the vault
automatically.

This is still not a production approval. The remaining gates are a verified
identity/account system, an operator retention and backup-deletion schedule,
and a real-user privacy review. Do not enable it for real personal data until
those are closed; the feature refuses to run outside an explicit
development/test/local environment.

## External model and telemetry processing

When an operator configures an LLM provider, ordinary consultation prompts and
the context assembled for generation may be sent to that provider. The
approved research Worker can send the authorized research request and relevant
retrieved evidence to its configured model provider after separate operator
approval. Provider retention, residency, training use, deletion and security
terms are outside this repository's control; review them before transmitting
personal or restricted data.

Sentry is optional and only active when explicitly configured by the deployer.
No repository analytics endpoint or default telemetry credential is shipped.
Operators must review the configured SDK capture and redaction behavior before
using real data. Do not log raw student prompts, provider exception bodies,
tokens or credentials.

## Synthetic and operator-supplied data

The checked-in sample data and knowledge corpus are labeled synthetic and must
not be used to make real admissions decisions. Deployers who import external
data are responsible for documented rights, source provenance, accuracy,
security, access control and deletion. A manifest or hash is traceability
metadata; it does not itself prove legal authorization or source authenticity.

## Historical Git objects

A removed database remains accessible in an older public Git object, as
documented in [SECURITY.md](SECURITY.md) and Issue #58. The repository did not
rewrite published history; current-tree removal is not a history purge.
