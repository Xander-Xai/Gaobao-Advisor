# Privacy

## What this demo stores

The running application writes only local runtime state:

* `data/demo.db` — the generated synthetic database;
* `data/reports/<session_id>/*.json` — conversation reports, when the report
  feature is used.

Both paths are gitignored and are not committed. The container generates the
database on start; `docker compose down -v` removes the demo volume.

## Historical exposure

A database file removed from the current tree remains reachable through an
older public Git object. The current tree contains no real admissions database
and no user messages. The open remediation record, the redacted forensic
summary and the owner-authorisation boundary are tracked in
[Issue #58](https://github.com/Xander-Xai/gaobao-advisor/issues/58) and
[SECURITY.md](SECURITY.md). Deleting a file from `master` is not a history
purge; this repository does not claim otherwise.

## No telemetry

No Sentry DSN, analytics endpoint or telemetry key ships with the repository.
The test suite only initialises Sentry when a developer explicitly configures
`SENTRY_DSN` in their own environment; CI does not.

## Session tokens

A session token is an HMAC of the session ID under `SESSION_SECRET`. It is a
capability over exactly one anonymous session, not an account. It contains no
profile data. Multi-worker deployments must set an explicit `SESSION_SECRET`.

## Not a production privacy notice

The default deployment handles synthetic data only. Anyone deploying this
software with real users must replace this document with their own privacy
notice and data-processing agreements before doing so.


## 用户跨任务记忆（默认关闭）

独立的用户偏好保险箱不会从对话或会话档案自动创建。只有用户显式创建独立凭证，
并经由 `/api/v1/memory/entries/{key}` 写入的非敏感偏好会进入新的长期记忆表。
存储时仅持有记忆 token 的 SHA-256 摘要；**偏好内容当前在数据库中不是加密字段**。
在完成加密、账号绑定、隐私审批之前，不得将此功能用于真实用户数据生产环境。

`DELETE /api/v1/memory/vault` 会撤销该 token 并删除实时数据库中的所有偏好；
`DELETE /api/v1/profile/{session_id}/data` 仅删除单个会话的实时数据，
不删除独立长期记忆。两种删除均不保证备份、在途模型上下文及外部处理者同步删除。
详见 [长期记忆文档](docs/cross-task-user-memory.md)。
