# TaskRun 证据复核与租约耗尽治理（2026-11-20）

> **状态：** 基于 #110 的 Draft PR 增量，全部使用 SYNTHETIC_DEMO。当前不是公网生产能力；无真实高考数据、真实模型效果和真实 Token/成本结论。

## 问题与处理

### 1. Worker 不得直接采信 Agent 自称成功

原 `taskrun_worker.execute_approved_run` 仅依赖返回对象的 `data_profile` 与 `status` 决定任务成功；对外部执行器返回的内容缺少独立于 Agent 轨迹的最终事实检查。

新增 `server/agent/taskrun_receipt_verifier.py`：
- 接收审批后冻结的 `DecisionCardProfile`，不从 Agent 的新 Prompt 或结果中重建授权；
- 在 Worker/服务端重新读取**当前合成 SQL** 的专业、两校录取和招生计划五条数据；
- 核对画像、两校历史最低分与位次、分差、计划、选科约束、专业门类、来源 ID 与演示声明；
- 只有所有事实和来源逐项一致才返回 `SUCCEEDED`；
- `NEEDS_REVIEW` 必须以 SQL 中真实缺失/约束冲突为前提，并且不能泄露部分学校比较和虚假来源；
- 任何不完整、篡改或过期的回执均按 **FAILED** 持久化，失败原因统一为 `agent_execution_failed`，不记录原始异常、密钥或模型内容。

这里的“独立”指**不采信执行器回传的文本或轨迹**，由服务端重新读取可信 SQL，并不声称使用了独立人工真值审核或不同的招生数据来源。两次读取之间数据如有变化，默认失败关闭。

### 2. 过期租约耗尽不能永久占用 RUNNING 配额

原任务在第 2 次执行租约过期后，`claim` 继续拒绝但状态仍是 `RUNNING`，从而长期占据操作员 8 个活跃任务配额。

修复后当过期租约已达到 `_MAX_ATTEMPTS=2` 时，同一个 `BEGIN IMMEDIATE` 事务内：
- 设置 `FAILED`、失败原因 `recovery_budget_exhausted`；
- 清除租约 token/deadline，增加版本；
- 拒绝第三次领取；旧 Worker 的任何终结提交仍被 CAS 拒绝；
- 释放活跃任务配额，但保留完整审计行与幂等键。

本实现不会主动取消仍在阻塞中的 Thread/LLM、不会继续 Deep Agents 内部 Token、也不承诺有副作用工具的 exactly-once。

## 复验

```bash
pytest -q tests/test_taskrun_receipt_verifier.py tests/test_taskrun_store.py tests/test_taskrun_deepagents_worker.py
ruff check server/agent/taskrun_receipt_verifier.py server/agent/taskrun_worker.py server/agent/taskrun_store.py tests/test_taskrun_receipt_verifier.py tests/test_taskrun_store.py
```

- 使用临时真实 SYNTHETIC_DEMO SQLite + Fake Runner，对合法回执、伪造完成、SQL 字段篡改、画像篡改、无依据拒答及有证据拒答进行回归；
- 使用真实 SQLite 租约过期注入，断言第 3 次领取拒绝、状态终结、过期 Worker 拒绝交回、配额恢复；
- Fake Runner 并非实际 Deep Agents / Claude Code 模型成功率。CI 状态以 PR 对应 SHA 为准。

## 仍未实现 / 无法声称

- 尚未实测真实模型任务成功率、真实 Tool Call Token/美元成本、并发长期稳定性及 Prompt/Skill 的 Holdout A/B 提升；
- 尚无真实志愿数据授权或人工审核的真实 Ground Truth；
- 不包含跨机数据库 HA、硬取消正在执行的模型、完整分布式恢复、通用 MCP、模型自主改写生产配置；
- 主分支仍需按 #103 → #105 → #106 → #107 → #108 → #109 → #110 → 本 PR 的顺序审查，不能跳过父 PR。
