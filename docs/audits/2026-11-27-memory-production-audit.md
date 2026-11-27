# 四层记忆生产化审计（2026-11-27）

> 审计对象：`server/agent/memory_manager.py`、`server/agent/chat_context.py`、
> `server/agent/durable_chat_context.py`、`server/agent/session_compaction_store.py`、
> `server/graph/nodes/memory.py`、`server/agent/taskrun_store.py`、`server/user_memory.py`、
> `server/routes/user_memory.py`、`frontend/src/components/layout/UserMemoryPanel.vue`，
> 以及相关迁移、隐私治理文档与测试。
> 口径：仅依据本仓库源码与本地执行结果；不引用外部运行结论，不把合成测试当作真实效果。

## 1. 审计结论摘要

| 层 | 审计前主要问题 | 本次处理 | 剩余风险 |
|---|---|---|---|
| L1 工作记忆 | 摘要/偏好可与 SQL 证据同屏，缺少"证据优先"的显式表达 | 增加受保护证据块；当前任务永不截断（原有） | 依赖模型遵守提示词，非代码强制 |
| L2 会话记忆 | 摘要可写入分数线等招生数字并以"（来源：…）"伪造引用；快照密钥不可轮换 | 摘要事实隔离；快照密钥环轮换；摘要失败降级显式化（原有） | 真实模型摘要质量未测 |
| L3 任务记忆 | 四类机制在不同文档中并列出现，易被当作"一种记忆" | 在架构文档中明确四类机制的职责/存储/生命周期边界 | 仍为单机机制，跨进程恢复未实现（有意） |
| L4 用户长期记忆 | 仅 bearer token，无身份绑定、无审计、偏好明文入库、无敏感数据拦截、无后台清理、无字段分类 | 身份绑定 + AES-GCM 加密 + 密钥轮换 + 同意/审计 + 字段白名单与敏感拦截 + worker 清理 + subject 擦除 | subject 为伪匿名；备份与外部处理者未覆盖 |

## 2. L4 用户长期记忆：逐项审计

### 2.1 审计前实现（HEAD `8b636db`）

- 身份：仅 `mem1_` + 64 字符随机 bearer token，库内只存 SHA-256 摘要。
  **任何拿到 token 的人即可读写该用户全部偏好**，无第二重绑定。
- 授权：无 consent 记录；`POST /api/v1/memory/vault` 直接创建。
- 审计：无任何记忆操作审计表。
- 分类：`ALLOWED_KEYS` 五个键，无数据分类；值只做长度/控制字符校验。
- 敏感信息：**无拦截**。高考分数、身份证、手机号、家庭信息均可写入。
- 加密：**明文入库**（PRIVACY.md 亦已声明）。
- 过期：有 `prune_expired`，但**未接入后台 worker**，只能人工跑脚本。
- 删除：条目删除、保险库擦除可用；无 subject 级（账号等价）擦除。
- 轮换：`rotate_token` 可用，但无"旧凭证失效"的端到端验证。
- 指令注入：写入值可包含"忽略以上指令"，读取时仅靠提示词声明不可信。

### 2.2 本次修复

| 项 | 实现 | 测试 |
|---|---|---|
| 身份绑定 | `X-User-Memory-Subject` + 服务端 HMAC 指纹（`server/auth.py:subject_fingerprint`），token 与 subject 必须同时匹配 | `test_stolen_token_without_matching_subject_is_rejected` |
| 显式同意 | `POST /api/v1/memory/vault` 必须 `consent=true`；记录 `consent_granted_at`/`consent_version` | `test_vault_creation_requires_explicit_consent` |
| 审计 | 新表 `user_memory_audit`（仅元数据：事件/键/版本/时间）；`GET /api/v1/memory/audit` | `test_audit_trail_records_events_without_values` |
| 字段白名单与分类 | `server/user_memory_policy.py`：`MEMORY_KEY_CLASS` 三类（展示/偏好/目标） | `test_explicit_write_persists_across_requests_without_plaintext_token` |
| 敏感数据拦截 | 写入前正则拦截：身份证、手机号、邮箱、银行卡、学号、高考分数/位次/投档线、家庭与财务信息、外链、指令注入；命中即拒且不回显、不落库 | `test_sensitive_or_injected_values_are_refused`（11 组） |
| 静态加密 | AES-GCM，密文 `enc:v1:<key_id>:<b64>`，AAD 绑定 `vault_id + memory_key`；密钥环支持退役密钥只读 | `test_preference_values_are_encrypted_at_rest`、`test_encryption_key_rotation_keeps_history_readable` |
| 后台清理 | `GAOBAO_USER_MEMORY_MAINTENANCE_ENABLED` 接入 Quality Worker 回收周期（同时清理过期审计行） | 见 `server/admin/worker.py::_prune_user_memory` |
| 账号等价擦除 | `erase_subject()`：擦除某 subject 的全部保险库与条目并立即失效凭证；CLI `scripts/erase_user_memory_subject.py` | `test_subject_erasure_removes_every_vault_of_that_subject` |
| 明文重加密 | `scripts/rekey_user_memory.py`：旧库明文条目重写为密文 | 见脚本；读取结果标记 `LEGACY_PLAINTEXT` |
| 指令注入纵深防御 | 读取时 `sanitize_for_prompt()` 中和指令式文本；偏好只进 user 角色 | `test_prompt_context_neutralizes_instruction_like_content` |

### 2.3 仍然未通过的门禁

1. **真实身份**：仓库无账号体系，`subject` 由客户端生成，仅能保证"不同 subject 不串数据"，
   不能证明真实自然人。
2. **备份保留与删除流程**：库内加密不覆盖数据库备份、文件系统快照、日志与模型供应商请求；
   备份保留周期未在代码或运维配置中固化（`docs/mysql-backup-restore.md` 在本分支尚未纳入，
   需运维补齐保留策略与到期删除演练）。
3. **真实模型记忆效果**：本次只提供离线合成评测，未做真实 provider A/B 与人工复核。

## 3. L2 会话记忆：逐项审计

| 项 | 审计前 | 本次处理 |
|---|---|---|
| 当前任务与 SQL 证据优先 | 当前任务不截断（已有），但旧轮次的来源标记会随窗口滑出 | 新增受保护证据块：被压缩轮次引用过的来源标记以服务端块形式追加到当前任务前，永不截断 |
| 摘要不得覆盖真实招生数据 | 仅靠摘要提示词约束 | 新增事实隔离：分数线/录取率/位次/百分比/保上类断言整行屏蔽；伪造 `（来源：…）` 引用整体移除；摘要提示词同步收紧 |
| 摘要失败安全降级 | 已有 `COMPACTION_UNAVAILABLE` + "上下文不完整" | 保留并补测；快照路径复用旧摘要时同样降级为 `SNAPSHOT_REUSED_UPDATE_FAILED` |
| 摘要版本与前缀一致性 | 已有 `covered_sha256 + policy_sha256 + CAS` | 保留；策略摘要已包含提示词与隔离策略，提示词变更即令旧快照失效 |
| CAS 并发更新 | 已有 | 保留 |
| TTL 与过期清理 | 快照 TTL + worker prune；偏好无 worker | 偏好新增 worker 接线 |
| 加密与密钥管理 | 单密钥 AES-GCM，无轮换 | 新增密钥环：`GAOBAO_CHAT_SNAPSHOT_KEY`（当前）+ `GAOBAO_CHAT_SNAPSHOT_PREVIOUS_KEYS`（退役只读） |
| 删除会话后的衍生数据 | 删除会话级联删除快照 | 保留；并确认审计/报告/回执同批删除 |

### 3.1 摘要触发阈值与近轮次数是否适合高报场景

- 生产默认：`KEEP_RECENT_ROUNDS=10`（开启压缩时）/ `MAX_HISTORY_ROUNDS=20`（未开启），
  `SUMMARY_TRIGGER_TOKENS=2000`，输入预算 8000（`GAOBAO_CHAT_*` 可覆盖）。
- 评审意见：**不能凭主观判断**认定某一组阈值"更适合高报"。本次改用受控对比
  （`benchmarks/memory_eval.py`，四臂 × 12 个冻结合成用例）给出可复现数据，
  结论见 `docs/evaluation/memory-eval-20261127.md`；真实 provider 效果仍为 NOT_MEASURED。

## 4. L3 任务记忆：四类机制边界

| 机制 | 存储 | 恢复语义 | 是否属于"用户记忆" |
|---|---|---|---|
| TaskRun 业务状态 | 隔离 SQLite `synthetic_task_runs` | 租约 + 取消栅栏 + 回执 | 否 |
| Agent Graph Checkpoint | 隔离 SQLite（SqliteSaver / `synthetic_checkpoint.py`） | 节点级续跑 | 否 |
| 工具执行证据 | 隔离 SQLite 账本 | digest 链校验后重放 | 否 |
| 跨任务经验记忆 | `synthetic_evidence_memory` | TTL + maker-checker + 指针 | 否（是"经验"不是"用户偏好"） |

审计结论：四者此前分散在多份文档中，易被合并理解为"一种记忆"。本次在架构文档中单列
"任务级四类机制"表格，明确不得互相替代，也不得与 L4 共用表、权限或提取路径。
**单机机制已满足当前业务需要，未实现分布式 Graph Checkpoint**（无真实跨进程恢复需求）。

## 5. L1 工作记忆

- 写入路径：`server/graph/nodes/memory.py` 在图末落库（消息 + 槽位 + `_query_state` +
  质量分 + 审阅票据），失败时只记 `persist_failed` 不污染回复。
- 读取路径：`llm_node` 用 `build_llm_context(state)` 注入当前任务（含 SQL 检索结果、
  RAG 来源状态、情绪与启发式）。
- 权威性：SQL 事实与已核验来源回执权威；LLM 散文、对话记忆与摘要不能覆盖。
- 删除：`DELETE /api/v1/profile/{session_id}/data` 级联删除会话数据（含快照）。
- 本次补充：跨任务偏好保险库**不属于**会话删除范围（会话是 session 级，偏好是 subject 级），
  在擦除响应中显式声明，避免用户误解。

## 6. 真实业务路径 / 开发实验路径 / 未接入模块

| 分类 | 内容 |
|---|---|
| 真实业务路径 | 聊天主链路、SQL 证据与来源标记、会话删除、worker 过期清理、快照读写（有开关） |
| 开发/测试路径 | 跨任务偏好保险库（`USER_MEMORY_ENABLED`，仅 dev/test/local）、摘要压缩与快照（开发环境显式开关）、偏好后台清理开关 |
| 合成实验路径 | TaskRun / DeepAgents / 合成证据记忆 / 合成 canary |
| 未接入或有意不做 | 真实模型记忆评测、账号体系、对话自动抽取长期记忆、分布式 Graph Checkpoint |

## 7. 本次验证记录

| 检查 | 命令 | 结果 |
|---|---|---|
| 记忆相关单测 | `python3 -m pytest tests/test_cross_task_user_memory.py tests/test_memory_evidence_governance.py tests/test_memory_eval_suite.py tests/test_session_compaction_snapshot.py tests/test_chat_context_compaction.py tests/test_memory_manager.py tests/test_memory_privacy_expiry.py -q --no-cov` | 全部通过 |
| 迁移 | `DATABASE_URL=sqlite:///... python3 -m db.migrate upgrade` | head = `20261127_0010`，`verify_tables(ALL_TABLES) == []` |
| 记忆评测 | `python3 -m benchmarks.memory_eval --scripted-smoke --out <file>` | 4 臂 × 12 用例，0 越权泄漏，0 SQL 证据丢失 |
| 全量回归 | `python3 -m pytest tests/ -q --no-cov` | 见交付说明 |

> 所有记忆测试均为**合成测试**：没有真实模型调用，没有真实学生数据，没有真实用户。
> 合成结果不证明真实模型质量、真实隐私合规或生产可用性。
