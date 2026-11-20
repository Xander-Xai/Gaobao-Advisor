# P30｜只读 Harness 的有界故障后重新决策（2026-11-20）

**状态：Draft 增量，基于 #133；未合并、未部署、未调用付费模型。** 本变更仅提升独立 `run_readonly_agent` 实验执行器的局部故障处理，**不**改变公共 `/chat`、Deep Agents Worker、生产任务取消或真实招生事实。

## 为什么需要它

之前所有工具异常立即终止：无法让 Planner 根据错误反馈决定下一步。P30 对明确标记 `retry_safe=True` 的合成只读工具引入 **opt-in** 故障反馈；默认仍 fail-closed。

- `HarnessLimits(max_recoverable_tool_failures=1)` 为显式恢复预算，不能超过总工具调用上限。
- 第一次异常记录一条不含异常文本、参数和密钥的 `recoverable_tool_failure` Trace，并向 Planner 提供无来源 ID 的结构化失败观察。
- 下一轮由 Planner 自己决定重试、改用其他允许的只读工具或拒答；所有尝试都计入工具步数和重复调用限制。
- 发生第二次异常、用量超标、重复调用越界、无证据结束或伪造失败字段，继续 fail-closed。
- 失败观察会通过现有 SQLite CAS 检查点持久化，重启后可在预算内恢复。只允许 SYNTHETIC_DEMO；即使工具名是只读，也必须通过 `retry_safe` 显式批准。
- **这里的“重新规划”指异常后的新一轮工具决策，不等于通用任务树规划、自主业务目标重编排或真实模型成功率提升。**

## 可验证测试

`pytest -q tests/test_harness_replanning.py tests/test_readonly_harness.py -o addopts=''`

覆盖：一次瞬态异常后成功重试；未双重授权时立即失败；恢复预算耗尽；恶意工具伪装故障；SQLite Checkpoint 恢复；原始异常不进入 Trace/上下文；预算参数无效拒绝。

## Eval 指标口径修正

另修复合成成绩表中一个容易影响求职表达的指标问题：原 `average_tool_calls_per_passing_case` 将**失败任务的工具调用数**也计入成功任务的平均值，导致混合成功/失败集指标失真。现在成功案例均值只统计通过的任务，并另外提供 `tool_calls_per_success_including_failures` 和 `model_decisions_per_success_including_failures`，后者明确把失败尝试的代价摊入每次有效交付。Token 每成功案例成本仍有意保留全部任务消耗。新增混合用例测试防止口径回退。

## 当前仍未完成的“部分实现”项

1. **Plan / Replan 全任务评估**：仍需真实 LLM 按冻结任务集重规划，并对照原版任务质量、工具调用次数、Token 和时延；本 PR 不伪造模型实验。
2. **真实 Cancel**：已有 POSIX 本地进程 Supervisor；远端模型已接收请求、第三方工具副作用和生产分布式任务不能承诺强取消或 exactly-once。
3. **Context / Memory**：已有来源保留、预算、合成 TTL 指针；缺真实模型质量对比与真实用户长期记忆完整闭环。
4. **Sandbox**：已有合成 Docker 受限工作区，尚未通过生产多租户逃逸/凭证/网络安全评估。
5. **Trace / Eval**：已有脱敏事件、CI 合成 Oracle；没有真实模型多次运行及专家 Ground Truth，不能宣传模型成功率或 Token 降本。

**Claim Gate**：只可表述“实现并测试可选择开启的合成只读工具故障恢复及 CAS 断点续跑”。不允许声称生产自动重规划、真实模型可靠性提升或业务交付完成。
