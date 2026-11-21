# P23｜Agent 半自主优化：开发集失败诊断、重复评测、管理员审核与模拟回滚

> **定位：仅演示 Sandbox 管理链路，不是生产发布系统。** 本切片堆叠在 Draft #126 之上，使用冻结合成高考数据和现有 8-case 模型实验报告格式；真实模型 Provider / 专家审核 / 真实用户业务结果仍未获得。任何 Admin 审批都不能将自报实验升级为独立核验的真实模型测量。

## 实现的闭环和实际边界

```text
重复配对模型报告（用户上传声明；不向 Provider 发请求）
   │
   ├──严格校验 2..5 完整对照、Prompt/模型/数据/工具版本、全量 Token
   │
   ├──仅 development 失败代码 → Counter/失败聚类 → UNTESTED 有界优化假设
   │       （禁止利用 Holdout 错误原因开发优化建议）
   │
   └──Holdout 独立质量/Token 门禁（PR #125 原有机制）
           │
           ├──REJECT_CANDIDATE → BLOCKED_BY_EVAL（任何管理员不能覆盖）
           └──REVIEW_REQUIRED → PENDING_ADMIN_ATTESTATION
                                    │
                             非提交人的两个不同 Admin 签名
                                    │
                      TWO_ADMIN_ATTESTATIONS_UNVERIFIED
                                    │
                         第四名 Admin 模拟激活
                                    │
                         DEMO_POINTER_ACTIVE
                                    │
                           Admin 显式撤销
                                    │
                       DEMO_POINTER_ROLLED_BACK
```

### 代码

- `benchmarks/synthetic_optimization_review.py`：用上轮 `compare_repeated_trials` 的**原始完整报告**重新校验，不接受前端自填评分；只从开发集结构化失败代码提出候选假设。候选配置仅允许冻结的 `evidence_first` 实验臂。整包报告做 SHA256 绑定，最多 160KB，不保留原始模型响应、对话、考生数据。
- `server/agent/synthetic_optimization_registry.py`：单服务 SQLite 审查表、冻结签名、可重入事务、固定上限 100 条。重复提交同一证据不会重置拒绝、签名和历史状态。
- `server/routes/agent_optimizer.py`：仅 Admin 可调用；复用现有默认关闭的 `ENABLE_SYNTHETIC_AGENT_EXPERIMENT`、非 production 限制和独立 TaskRun Journal。不会让 Operator / Viewer / 浏览器自动调用付费模型。
- `server/main.py`：仅注册受控管理端路由；**原 `/chat`、Deep Agents Worker 的实际 Prompt/调用环境均不读取模拟指针**。

### HTTP 契约

全部前缀：`/api/v1/admin/agent-optimizer`。请求必须携带当前 Admin Bearer token，且先配置独立的 `SYNTHETIC_TASKRUN_JOURNAL` 和实验开关。

| 方法/路由 | 用途 |
|---|---|
| `POST /proposals`，Body: `{"pairs":[[baseline_report,candidate_report],...]}` | 由服务端计算评测与失败分类，记录指纹 |
| `GET /proposals/{id}` | 审阅结果/签名/事件（不包含原始输入报告） |
| `POST /proposals/{id}/review`，Body: `{"verdict":"attest_reviewed"}` 或 `"reject"` | 不可撤销的账号签署，提交人不得自审 |
| `POST /proposals/{id}/simulate` | 第四名管理员启用**演示指针**，不改变运行时 |
| `GET /demo-pointer` | 查询仅供演示的候选指针 |
| `POST /demo-pointer/rollback` | 删除候选指针并保留审计事件 |

可见的字段 `effective_public_chat_prompt="UNCHANGED"`、`effective_taskrun_deepagents_prompt="UNCHANGED"`、`production_deployment_authorized=false` 是**硬边界**。成功模拟激活不等于生产灰度。若要真实灰度，必须另立风险审批、独立验证真实模型来源、人工专家标注、版本锁定、监控以及真实配置灰度/回滚流程。

### 本轮测试

```bash
pytest -q tests/test_synthetic_optimization_review.py --no-cov -o addopts=''
pytest -q tests/test_repeated_model_experiment.py tests/test_harness_optimizer.py --no-cov -o addopts=''
ruff check .
ruff format --check .
```

覆盖：发展集失败码和 Holdout 错误信息隔离、捏造 Scripted 运行拒绝、重复 ID/不完整账单/过大数据拒绝、质量退化拒绝、同证据幂等、管理员禁止自审、不同两人审批、审批不可反悔、第四人演示指针、回滚可审计、未登录/角色越权、实验关闭和生产禁用。测试中使用**伪造的模型声明报告**验证门禁代码，**不等于真实模型成功**；无真实密钥/外部调用。

## 尚未完成，继续保留 P0/P1

1. **真实模型端到端 A/B**：需要经用户授权、可复现 Provider/模型版本、费用限额与对账；程序无法凭空生成真实 Token 效率提升证据。
2. **专家 Ground Truth 与真实业务北极星**：当前全部合成数据，管理员签名不是招生专家认证。
3. **完整 Agent Trace**：目前消费的是已评分的案例级指标，不包含模型内部工具序列/上下文历史；失败代码根因分析仍只是开发集的统计与有界规则建议。
4. **真实灰度/生产回滚**：本轮只是 SQLite 中的**不被运行时消费**的演示指针，不能宣称上线、灰度或 rollback 生产配置。
5. **独立来源签章、隔离执行、多租户与长期数据治理**：当前 SQLite 仅单服务 Demo，不能证明不可篡改/高可用。

本切片不推动将所有 Agent 数据扔进向量库；不提供任意 Prompt 文本、Shell/MCP/文件写权限；无需新增第三个 Agent Framework。
