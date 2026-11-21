# 跨任务用户长期记忆（P31：显式同意的独立记忆保险箱）

> 交付范围：可运行的 **API + SQLite/MySQL 持久化 + Chat LLM 上下文接线 + 权限测试**。
> 默认关闭；未宣称公网生产就绪。与 `server/agent/governed_evidence_memory.py`
> 的「合成任务证据指针」是**两种完全独立的数据**。

## 1. 什么可以记、什么不能记

- 仅用户主动通过 API 添加/修改、带 TTL 的偏好：`preferred_regions`、
  `interested_majors`、`career_goal`、`study_goal`、`response_style`。
- `response_style` 只允许 `concise` / `detailed` / `comparison`。
- 每条文本最多 120 字符；TTL 为 1–365 天，默认 180 天；**不应**保存高考分数、身份证、电话、家庭情况、对话原文、模型推断或未经核验的招生事实。
- 当前 API 只校验允许的分类、长度、控制字符和 `response_style` 枚举；没有通用语义敏感信息识别，不能保证技术上阻止用户把上述内容误写入偏好字段。该限制是生产上线前必须补齐的数据治理门槛。
- **不自动从聊天记录抽取或长期保存用户信息**。聊天历史、会话档案、
  合成证据引用均不会隐式成为用户长期记忆。
- LLM 只接收由当前请求独立记忆凭证授权、未过期的偏好（最多 5 类）。
  把偏好当不可信数据，不把它当政策证据，不覆盖当前任务的 RAG/SQL。
- 未配置 LLM Key 时确定性模板不使用记忆做个性化推断；API 仍支持读取。

## 2. 启用与迁移

```bash
# 默认关闭，先在隔离的开发环境验证
export USER_MEMORY_ENABLED=true

# SQLite 演示环境启动时 create_all；MySQL 必须执行迁移
python -m db.migrate upgrade
# 预期 Alembic head: 20261120_0006
```

HTTP 接口使用独立请求头 `X-User-Memory-Token`，**不是**
`Authorization: Bearer <session HMAC>`，也不把它和 `session_id` 捆绑。
因此一个记忆凭证可以在多个新开的聊天会话中显式复用。

## 3. 最小操作流程

以下命令请只在本地安全环境执行；**切勿把真实 token 放进公开日志、命令历史、截图或仓库**。

```bash
# 1. 用户主动创建保险箱；响应只在创建时返回完整 token
curl -sS -X POST http://localhost:8000/api/v1/memory/vault
# 保存响应中的 memory_token 到受保护的凭证管理器

# 2. 新增偏好：首次 expected_version=0
curl -sS -X PUT http://localhost:8000/api/v1/memory/entries/preferred_regions \
  -H 'Content-Type: application/json' \
  -H 'X-User-Memory-Token: <TOKEN>' \
  -d '{"value":"广东","expected_version":0,"ttl_days":180}'

# 3. 查看（含 version、expires_at、来源 EXPLICIT_USER）
curl -sS http://localhost:8000/api/v1/memory/entries \
  -H 'X-User-Memory-Token: <TOKEN>'

# 4. 在任意新的聊天会话中，额外附上该请求头即可授权检索
curl -sS -N -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Memory-Token: <TOKEN>' \
  -d '{"session_id":"fresh-client-placeholder","scene":"gaokao","message":"请简要比较一下选择专业的思路"}'

# 5. 如需修改，使用 GET 得到的当前 version 进行乐观锁更新（冲突 409）
# 6. 逐条忘记：DELETE /api/v1/memory/entries/{key}
# 7. 凭证轮换：POST /api/v1/memory/vault/rotate（旧 token 立刻失效）
# 8. 全部忘记：DELETE /api/v1/memory/vault（删除明文偏好和 token 摘要）
```

接口状态码：未启用 503、缺失/错误凭证 401、版本冲突 409、
字段/TTL 非法 422、条目不存在 404。创建/轮换响应含明文 token；
所有记忆路由返回 `Cache-Control: no-store`。
token 为 384 位随机数；数据库仅保存 SHA-256 摘要。

## 4. 一致性与隐私边界

1. 表 `user_memory_vaults` 与 `user_memory_entries` 独立于聊天和合成任务表；
   `vault_id + memory_key` 唯一，更新走 SQL 版本比较（CAS）。
2. 只有携带正确独立凭证的请求能读/写该用户的记忆。无凭证的新会话只能获得空记忆。
3. 过期后不再被读取；更新过期值必须重新以版本 0 显式确认。
   当前过期行采用**逻辑排除、按键写入时清理**，尚未提供定时全表清理作业。
4. `DELETE /profile/{session_id}/data` 仅删除会话数据，不会删除独立用户记忆；
   `DELETE /memory/vault` 删除整个保险箱的实时 SQL 数据，不会删除会话报告。
5. 删除/轮换不承诺清理备份、外部处理者、已经发送给模型提供商的上下文、
   或在途聊天请求的内存副本。正式运营前需要数据处理协议和保留周期流程。

## 5. 生产上线前仍必须通过的门槛

- **真实账号体系与身份绑定：** 目前是独立 capability bearer token，不具备登录找回、
  多设备账号恢复、身份核验、家庭成员协作授权或账号级删除证明。丢失 token 无法找回。
- **个人信息治理：** 当前偏好以数据库明文保存，仅适合经审批的非敏感偏好演示。
  正式用户数据需加密/KMS、密钥轮换、同意记录、访问审计、备份清除与隐私政策评审。
- **用户界面：** 当前只提供 API；前端暂无登录/显式授权/管理与遗忘页面。
- **模型与真实决策：** 无真实招生数据质量验收、长期用户追踪准确率证据或线上 SLO。
  该功能不会证明志愿推荐结果可靠。切勿把偏好记忆与事实证据混同。
- **运营安全：** 需要路由级注册速率限制、异常监控、成本限制、安全扫描与端到端回归验收；
  现阶段沿用应用公共 API 限流。

## 6. 回归命令

```bash
pytest -q -o addopts='' tests/test_cross_task_user_memory.py
pytest -q -o addopts='' tests/test_migrations.py
ruff check server/user_memory.py server/routes/user_memory.py tests/test_cross_task_user_memory.py
```

`USER_MEMORY_ENABLED=false` 是回滚开关：关闭后记忆 API 拒绝读写，
聊天不自动读取记忆，不删除已存在的保险箱数据。
