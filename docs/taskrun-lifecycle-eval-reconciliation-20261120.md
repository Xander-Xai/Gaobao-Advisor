# TaskRun 生命周期评测与租约巡检（2026-11-20）

> 实验性质：全部为 `SYNTHETIC_DEMO` 虚构院校/招生数据。P8 依赖 P7 PR #111，且其之前的 Draft PR #110→#103。不是生产应用、真实招生正确率、真实 Agent 模型表现或已获人工审定 Ground Truth。

## 为什么要做

原 `benchmarks/decision_card_business_eval.py` 8 案例验证的是业务卡片函数；没有一份统一报告同时验证 **create → maker/checker approve → lease claim → deterministic worker → trusted SQL verifier → durable receipt**。

此外，P7 在再次 `claim(run_id)` 时会终结两次重试耗尽的过期任务，但如果不再有人领取该 ID，旧状态可能长期占用 operator 活跃任务配额。

## 新增能力

1. `benchmarks/taskrun_lifecycle_eval.py`：复用已冻结的 8 个合成案例（development 4 / holdout 4）；每条真正创建、幂等重放、异人审批、执行 Worker、从 journal 重读持久化回执，再经独立 SQL oracle 评分。**有效历史比较交付数与正确拒答数分开统计**；不把安全拒答伪装为完成的志愿方案。
2. 每个案例记录 `tool_calls`、确定性策略步数、失败原因、尝试次数、局部进程耗时；报告总体和 holdout 通过数，并显示纯本机样本的经验 P95（不是生产延迟/SLA）。
3. `provider_prompt_tokens` / `provider_completion_tokens` / `provider_cost_usd` 均是 `null`；`model_autonomous_task_success_rate`、`real_user_north_star_metric` 为 `NOT_MEASURED`。CI 不触发真实 LLM。
4. `SyntheticTaskRunStore.reconcile_exhausted(max_runs)`：事务内有界扫描 `status=RUNNING`、`attempts>=2`、**租约已到期**的记录，设置 `FAILED/recovery_budget_exhausted`、清空旧租约、回收活跃任务容量。仍可重试的首轮过期任务和未过期任务不动。批次 1～200，可反复运行。
5. `python -m server.agent.taskrun_maintenance`：管理员在可信本地环境显式执行巡检；开发环境需启用合成实验、禁用出站抓取，生产环境禁止。无 Web API 入口、无 Shell 工具暴露给 Agent、无模型调用或付费。

## 运行与验收

```bash
# 离线合成数据、临时数据库与临时 journal，完全无 Key
python -m benchmarks.taskrun_lifecycle_eval --out /tmp/taskrun-lifecycle.json

# 只对开发环境已存在的合成 TaskRun journal 扫描一次
export APP_ENV=development
export ENABLE_SYNTHETIC_AGENT_EXPERIMENT=true
export ENABLE_OUTBOUND_FETCH=false
python -m server.agent.taskrun_maintenance --journal /tmp/gaobao-agent-journal.db --max-runs 100

# 针对性测试
pytest -q tests/test_taskrun_lifecycle_eval.py tests/test_taskrun_reconciliation.py
```

CI 在现有 `agent-harness-synthetic-evidence` artifact 中增加 `taskrun-lifecycle-8case-baseline.json`。测试包含 8 条实际 TaskRun 生命周期、人工审批角色隔离（合成用户）、两次租约耗尽、批量上限、旧 Worker CAS 拒绝、CLI 生产拒绝。

## 指标边界

| 指标 | 可用范围 |
| --- | --- |
| verified_outcomes | 合成业务任务 + 同库独立查询评分，包含正确拒答 |
| historical_comparisons_delivered | 真正给出两个虚构学校历史比较的合成案例数 |
| safe_abstentions | 观察到证据缺失后拒绝输出比较的合成案例数 |
| tool_calls | 确定性 Worker 中计数的 SQL 工具调用，不等于 LLM 自主步长 |
| empirical_local_p95_ms | 单次 CI 进程少样本粗略时延，不用于 SLA 或生产容量 |
| real_user_north_star_metric | NOT_MEASURED |
| provider token / cost | null，没调用外部 LLM |

此套件是 **离线可靠性评测基础**，不是同一模型与 Prompt 改动的真实 Holdout A/B，也不证明人工 Ground Truth、复杂 Agent 自主优化或高考录取预测质量。

下一步如需真实模型实验，须由操作员单独授权 API Key、费用预算、版本固定和离线 Holdout；不能偷偷在 CI / Web 后台调用付费模型。巡检也不是调度器，暂不宣称跨机器自动恢复或 Exactly-Once。
