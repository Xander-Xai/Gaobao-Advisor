# 跨任务用户长期记忆（显式同意的独立记忆箱）

> 全部内容基于合成演示数据（SYNTHETIC_DEMO），描述的是开发/测试环境中的可选特性，
> **不代表当前生产验证结果**。
> 交付范围：**API + MySQL/SQLite 持久化 + Chat LLM 上下文接线 + 权限/加密/审计测试**。
> 默认关闭；未宣称公网生产就绪。与 `server/agent/governed_evidence_memory.py`
> 的「合成任务证据指针」是**两种完全独立的数据**。
> 2026-11-27 更新：加入身份绑定、静态加密与密钥轮换、显式同意、审计、字段分类与敏感数据拦截。
> 架构总览见 [分层记忆生产化治理](architecture/memory-production-governance-20261127.md)。

## 1. 什么可以记、什么不能记

- 仅用户主动通过 API 添加/修改、带 TTL 的偏好：`preferred_regions`、
  `interested_majors`、`career_goal`、`study_goal`、`response_style`。
- `response_style` 只允许 `concise` / `detailed` / `comparison`。
- 每条文本最多 120 字符；TTL 为 1–365 天，默认 180 天。
- **敏感个人信息一律拒绝写入**（2026-11-27 起）：高考分数/位次/投档线、身份证/护照号、
  手机号、邮箱、银行卡、学号、家庭与财务信息、外部链接、指令注入式文本。
  命中即返回 422，既不回显原值，也不落库。
- 字段带数据分类（`PRESENTATION` / `PREFERENCE_CATEGORICAL` / `PREFERENCE_GOAL`），
  由 `server/user_memory_policy.py` 单一维护。
- **不自动从聊天记录抽取或长期保存用户信息**。聊天历史、会话档案、
  合成证据引用均不会隐式成为用户长期记忆。
- LLM 只接收由当前请求独立记忆凭证**与 subject 同时授权**、未过期的偏好（最多 5 条）。
  偏好以 user 角色注入并标注"不可信输入"，不覆盖当前任务的 RAG/SQL，不作招生事实。

## 2. 身份模型（最小可靠身份）

仓库**没有账号体系**。本次不引入大型 IAM，而是采用可验证的最小方案：

| 凭证 | 作用 | 存储 |
|---|---|---|
| `X-User-Memory-Token`（`mem1_` + 64 字符随机） | 证明"持有保险箱秘密" | 仅存 SHA-256 摘要 |
| `X-User-Memory-Subject`（8–128 位 `[A-Za-z0-9_-]`） | 证明"是同一个人" | 仅存服务端 HMAC 指纹 |

两者任一不匹配返回 401（不区分"不存在"与"不匹配"）。subject 由客户端生成并保存在
浏览器本地（非凭证），因此**一个 token 被盗但没有同一 subject 仍不可用**。

诚实边界：subject 是**伪匿名标识**，不能证明真实自然人，不能防止同一人多套记忆，
也不能防止多人共用一个 subject。它只能保证"不同 subject 之间不串数据"。

## 3. 启用与迁移

```bash
# 默认关闭，先在隔离的开发环境验证
export APP_ENV=development
export USER_MEMORY_ENABLED=true
# 生产必须显式提供加密密钥（32 字节 hex）；开发环境缺失时使用本地密钥文件或进程内临时密钥
export USER_MEMORY_ENCRYPTION_KEY=<64 hex>
# 密钥轮换：旧密钥保留为只读退役密钥，历史条目仍可解密
export USER_MEMORY_ENCRYPTION_PREVIOUS_KEYS=<64 hex>

python -m db.migrate upgrade     # head: 20261127_0010
```

## 4. 最小操作流程

> 请只在本地安全环境执行；**切勿把真实 token 放进公开日志、命令历史、截图或仓库**。

```bash
# 1. 显式同意后创建保险箱；完整 token 只在创建响应中返回一次
curl -sS -X POST http://localhost:8000/api/v1/memory/vault \
  -H 'Content-Type: application/json' \
  -H 'X-User-Memory-Subject: sub_<client-generated-id>' \
  -d '{"consent":true}'

# 2. 新增偏好：首次 expected_version=0
curl -sS -X PUT http://localhost:8000/api/v1/memory/entries/preferred_regions \
  -H 'Content-Type: application/json' \
  -H 'X-User-Memory-Token: <TOKEN>' \
  -H 'X-User-Memory-Subject: sub_<client-generated-id>' \
  -d '{"value":"广东","expected_version":0,"ttl_days":180}'

# 3. 查看（含 version、expires_at、data_class、storage）
curl -sS http://localhost:8000/api/v1/memory/entries \
  -H 'X-User-Memory-Token: <TOKEN>' -H 'X-User-Memory-Subject: sub_...'

# 4. 审计事件（仅元数据，不含偏好内容）
curl -sS http://localhost:8000/api/v1/memory/audit \
  -H 'X-User-Memory-Token: <TOKEN>' -H 'X-User-Memory-Subject: sub_...'

# 5. 在任意新的聊天会话中附带两个请求头即可授权检索
curl -sS -N -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Memory-Token: <TOKEN>' -H 'X-User-Memory-Subject: sub_...' \
  -d '{"session_id":"fresh-client-placeholder","scene":"gaokao","message":"请简要比较一下选择专业的思路"}'

# 6. 修改使用 GET 得到的当前 version（冲突 409）
# 7. 逐条忘记：DELETE /api/v1/memory/entries/{key}?expected_version=<当前版本>
# 8. 凭证轮换：POST /api/v1/memory/vault/rotate（旧 token 立刻失效）
# 9. 全部忘记：DELETE /api/v1/memory/vault
```

接口状态码：未启用 503、缺失/错误凭证或 subject 401、版本冲突 409、
字段/TTL/敏感信息非法 422、条目不存在 404、未显式同意 422。
所有记忆路由返回 `Cache-Control: no-store`。

## 5. 加密、审计与生命周期

1. **静态加密**：偏好值以 AES-GCM 密文存储，格式 `enc:v1:<key_id>:<base64>`，
   AAD 绑定 `vault_id + memory_key`，因此密文无法跨保险库或跨字段搬运。
2. **密钥轮换**：`USER_MEMORY_ENCRYPTION_KEY` 为当前密钥，
   `USER_MEMORY_ENCRYPTION_PREVIOUS_KEYS` 为只读退役密钥。轮换后新写入用新密钥，
   旧条目仍可读；退役密钥被移除后，对应条目**读取失败并跳过**（不返回明文、不猜测）。
3. **旧库明文**：`20261127_0010` 之前的条目保持明文以免丢数据，读取结果标记
   `LEGACY_PLAINTEXT`；用 `python -m scripts.rekey_user_memory` 重写为密文。
4. **审计**：`user_memory_audit` 记录 `VAULT_CREATED / MEMORY_WRITTEN / MEMORY_UPDATED /
   MEMORY_DELETED / MEMORY_EXPIRED / CREDENTIAL_ROTATED / VAULT_ERASED`，
   **只含事件、键、版本与时间**，不含偏好值、凭证或 subject。审计行保留 365 天后清理。
5. **过期清理**：条目 TTL 1–365 天；`GAOBAO_USER_MEMORY_MAINTENANCE_ENABLED=true` 时，
   Quality Worker 回收周期会物理删除过期条目与过期审计行；也可运行
   `python -m scripts.prune_user_memory --limit 200`。
6. **版本与并发**：`vault_id + memory_key` 唯一，更新走 SQL CAS；
   保险库级单调版本永不重置（删除、过期清理、凭证轮换都不重置）。
7. **凭证轮换**：`POST /vault/rotate` 原子失效旧 token，旧 token 立即无法读写删除擦除。
8. **删除边界**：会话删除（`DELETE /api/v1/profile/{session_id}/data`）**不**删除跨任务偏好
   （会话是 session 级，偏好是 subject 级）；偏好用 `DELETE /api/v1/memory/vault` 整体擦除，
   或由运维用 `python -m scripts.erase_user_memory_subject --subject <sub>` 擦除某人的全部保险库
   （账号删除的等价路径）。

## 6. 生产上线前仍必须通过的门槛

- **真实账号体系与实名身份绑定**：当前只有伪匿名 subject，缺少登录找回、身份核验与
  账号级删除证明。**这是保持生产关闭的首要原因。**
- **备份保留周期与删除流程**：库内加密不覆盖数据库备份、文件系统快照、日志与模型供应商
  请求。备份保留策略与到期删除必须由运维固化并演练。
- **隐私评审与数据处理协议**：偏好与摘要会随提示词发送给模型供应商，属于"已授权但外部处理"。
- **用户界面验收**：前端面板已支持创建/读写/删除/审计展示，但真实用户可用性与
  "显式同意"的措辞需法务与用户测试确认。
- **模型与真实决策**：无真实招生数据质量验收、长期用户追踪准确率证据或线上 SLO。
  该功能不会证明志愿推荐结果可靠。切勿把偏好记忆与事实证据混同。
- **真实模型记忆效果**：`docs/evaluation/memory-eval-20261127.md` 只提供合成层证据，
  真实 provider A/B 仍为 NOT_MEASURED。

## 7. 回归命令

```bash
pytest -q -o addopts='' tests/test_cross_task_user_memory.py
pytest -q -o addopts='' tests/test_memory_eval_suite.py tests/test_memory_evidence_governance.py
pytest -q -o addopts='' tests/test_migrations.py
ruff check server/user_memory.py server/user_memory_policy.py server/user_memory_crypto.py \
  server/routes/user_memory.py tests/test_cross_task_user_memory.py
```

只有明确的 development/dev/test/testing/local `APP_ENV` 才能启用此偏好实验；
生产、staging、未知及未设置环境均拒绝启用。偏好内容只放在 user-role 上下文，不进入 system-role 指令。

`USER_MEMORY_ENABLED=false` 是回滚开关：关闭后记忆 API 拒绝读写，
聊天不自动读取记忆，不删除已存在的保险箱数据。
