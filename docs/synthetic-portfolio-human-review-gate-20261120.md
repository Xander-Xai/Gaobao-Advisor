# P17｜独立管理员双签：对**虚构历史证据**留痕，非真实招生 Ground Truth

> 本 PR 实现的是**人工审核入口与权限约束**。真实用户、真正招生专家以及真实招生政策的审核**尚未发生**；测试中账号与审核行为全部为合成。请勿把 `TWO_ADMIN_SIGNOFFS_SYNTHETIC_ONLY` 误写成“高考志愿方案人工通过”。

## 为什么需要

此前 #120 已有独立 SQL 规则 Oracle，但真正的人类审批仍只有 TaskRun 执行前的 maker-checker。审批 Agent 是否允许运行，与审核 Agent 的输出是否符合业务规则，是两种不同的职责；且此前的 `groundtruth_pipeline` 仅保存审核者名字字符串，不能证明审核者由系统认证。

## 已实现的受控链路

1. Operator 创建**冻结虚构组合** TaskRun；另一名 Admin 审批执行。
2. Worker 只读 SQL、经过双重 Oracle 核验后，持久化结果，`SUCCEEDED` 只代表完成历史资料比较。
3. `GET /api/v1/admin/agent-taskruns/runs/{id}/review` 返回 `receipt_sha256` 和当前复核状态，非 owner 的普通 operator 不可读取。
4. **两名不同且登录认证的** Admin 通过 `POST .../runs/{id}/review` 分别提交 `verdict=verified_historical_only|reject` 和精确的 `receipt_sha256`。两人均不得是 TaskRun Owner 或最初的执行批准人；Admin 身份来自既有 RBAC 登录 Token。
5. SQLite journal 中另建 `synthetic_portfolio_reviews` 表：事务写入，唯一约束 `(run_id, reviewer)`，只能追加**不可改变**的首次裁决。相同重复提交幂等，反悔或第三个复核账号均被拒绝。
6. 结果与 `run_id + request SHA + result` 绑定并规范化 SHA256，拒绝过期、串任务或篡改的回执；不允许在非终态审核。
7. 两人均认可时状态 `TWO_ADMIN_SIGNOFFS_SYNTHETIC_ONLY`；任何人拒绝即 `REJECTED_SYNTHETIC_EVIDENCE`，并非“多数服从少数”。
8. API 输出始终保留 `real_admissions_ground_truth=false`、`professional_policy_review_complete=false`、`production_release_authorized=false`；签名证明某管理员账号提交了这个声明，**并不证明是合格招生专家或核验了真实规则**。

## 隔离 HTTP 验收

```bash
pytest -q tests/test_portfolio_review_gate_e2e.py
```

单个隔离子进程创建虚构 Operator、执行批准 Admin、两名复核 Admin、第三复核 Admin 和 Viewer，通过实际 FastAPI 与 SQLite 进行：有审核/无审核、401/403、Owner/执行批准人回避、SHA 不匹配拒绝、超范围字段拒绝、两人逐项复核、同人重试幂等、改判拒绝、第三人拒绝、意见不一致 fail-closed、跨实例持久化、未完成任务禁止复核、实验关闭或生产环境禁用。

## 尚缺

- 获得真实招生政策或公开可再分发数据来源和许可，建立每个省份及招生年份的权威快照；
- 正式组织专业人员审核，把其真实资质、原始标注和溯源证据留在单独的正式系统；
- 在合法真实任务与审核 GT 基础上执行真实 LLM A/B 与 Token/成本测量；
- Deep Agents 跨进程内部状态恢复、受控 Worker 真正强制取消和生产级隔离；
- 所有增量仍为 GitHub Draft PR 栈，未合并 `master`。
