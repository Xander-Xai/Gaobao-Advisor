# P22｜受控证据记忆：来源、过期、冲突、删除与防陈旧

> 仅限 **SYNTHETIC_DEMO** 已通过双 Admin 回执签署的虚构招生历史数据，不是用户画像记忆、真实招录政策或生产 Agent 自动长期记忆。基于 Draft #125，需按依赖序审查。

## 为什么不直接把 LLM 摘要写进向量库

原 `MemoryManager` 对话摘要只压缩最近/较早对话，不具备权威事实确认、TTL、冲突消解、撤销等流程。尤其招生数据不适合把模型推断反复复用为事实。本切片**不修改主线 /chat 的总结行为**，而是给经过现有 TaskRun 审批和 SQL Oracle 确认的**虚构多校组合证据**增加一个可控、可回收的索引。

## 已实现

1. 新 `SyntheticEvidenceMemoryStore`：在隔离的 `SYNTHETIC_TASKRUN_JOURNAL` SQLite 中建立 `synthetic_evidence_memory` 表，**只保存指向原 TaskRun 的指针、哈希、发布管理员和有效期**，不复制模型总结或考生个人数据。
2. 发布门禁：仅 `SUCCEEDED` 的 `portfolio` 任务；当前回执必须满足两名不同 Admin 的正向签署（已有 maker-checker 验证）；现有 SQL 校验器会重新读取 `SYNTHETIC_DEMO` 数据并逐字段核验；失败时不入库。
3. 每次读取：校验任务归属和 RBAC、原回执 SHA256、合成数据档案、双审核状态、**当前实际 SQL**；数据变更即拒绝输出缓存结果。没有 `REVIEW_DECLARED` 自动升级为真人高考专业审核。
4. 生命周期：有效期 60 秒–30 天；同一个固定候选组合（由规范 JSON 哈希得到 key）存在不同来源时明确 `409`；重复发布同一来源不延长 TTL；过期后必须显式重新发布；主动删除索引，不删除历史任务收据和管理员审核记录。
5. 隔离：原 `/chat` **不注入**这些指针或结果，绝不自动采集考生个人数据。只通过 default-off 的 Admin Experiment API 暴露，`GET /evidence-memory` 只列当前认证身份**本人**的已验证索引，最多 50 条，陈旧条目计入 blocked 数、不作为事实输出。
6. 数据范围：只返回合成的历史比较与排除原因；无当年真实录取概率、实际志愿方案或用户有效交付率。

## API（必须 ENABLE_SYNTHETIC_AGENT_EXPERIMENT=true 且非 production）

| 操作 | HTTP | 权限 |
|---|---|---|
| 发布 | `POST /api/v1/admin/agent-taskruns/runs/{run_id}/memory`，JSON `{"ttl_seconds":86400}` | Admin |
| 读取 | `GET /api/v1/admin/agent-taskruns/runs/{run_id}/memory` | TaskRun owner 或 Admin |
| 列表 | `GET /api/v1/admin/agent-taskruns/evidence-memory` | 只看本人，Operator 以上 |
| 删除 | `DELETE /api/v1/admin/agent-taskruns/runs/{run_id}/memory` | TaskRun owner 或 Admin |

**完整流程**：创建合成 portfolio TaskRun → 异人管理员批准执行 → 运行确定性 SQL → 两名不同管理员审核原始冻结收据 → 第三次显式 Admin 发布受控记忆 → owner 检索时同步重核。上述账号审核是身份鉴别操作，**不能证明独立专业专家完成了真实政策审核**。

## 回归测试（不访问外部 LLM）

```bash
python -m pytest tests/test_governed_evidence_memory.py -q --no-cov --override-ini addopts=''
python -m pytest tests/ --tb=short --cov-fail-under=70
ruff check .
ruff format --check .
```

覆盖：未登录/低权限/跨 Owner 禁止；未审核、审核拒绝不能发布；重复发布不得延长；相同画像不同来源冲突；实际数据库某条源记录变化后，所有旧记忆读取必须 `409` 并在列表中被剔除；恢复来源后正常；TTL 到期拒绝、重新显式发布；主动删除仅清除指针，TaskRun 与审计仍存在；非开发环境与 Feature Flag 关闭拒绝访问。均为合成隔离环境。

## 仍未实现，不能提前写进简历

- **真实用户长期记忆**：用户显式同意、个人隐私删除范围、源会话撤销与备份清理、GDPR/PIPL 机制尚未接入。本切片删除的只是**本表的记忆索引**，不是底层任务、日志、数据库或其它复制品。
- **跨真实政策版本**：需要权威部门招生政策、适用年份、官方发布/废止时间、法律和专业审核流程。
- **LLM 自动提取与使用长期记忆**：禁止模型自行写入未核验推断；后续应独立设计 opt-in 提取、来源级授权和定量评测，不能直接修改默认生产聊天链路。
- **跨宿主机可用性和保密数据加密**：当前 SQLite 是独立本地单服务演示，不能当生产分布式记忆服务。
- **真实模型与效果**：无模型 Token、业务正确率、成功率提升或人工专家真值，继续保持 NOT_MEASURED。
