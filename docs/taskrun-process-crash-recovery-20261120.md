# P12｜真实子进程异常退出与 TaskRun 恢复回归（合成、只读）

> 与 P10 的任务取消不同，本切片验证的是**独立进程在租约领取后异常退出，下一独立 Worker 能够重新领取并重新执行只读任务**。仍不是恢复 Deep Agents 内部模型消息、真实生产集群高可用或带副作用的 exactly-once。

## 实际故障路径

1. 初始化**隔离合成招生 SQLite** 和独立的 TaskRun journal，真实创建任务并由不同身份审批。
2. **启动真实子进程**领取任务租约，写出 lease token，然后立即执行 `os._exit(17)`，模拟运行中无清理地崩溃。
3. 验证 journal 仍为 RUNNING，第一次没有写入结果。
4. 在**专用测试 journal** 通过 SQL 把租约截止时间推进到过去（避免在 CI 中真正等待最低 15 秒），检查旧 Worker 的 token 无法写入完成回执。
5. 启动第二个真实子进程 `python -m server.agent.taskrun_worker`，重新 claim，执行独立 SQL 事实查询与回执复核后完成。
6. 检查 attempts=2，最终持久化为 SUCCEEDED，保留已发生的重试计数；这是只读任务 *at-least-once* 的恢复语义。

## 可复现命令

```bash
python -m benchmarks.taskrun_crash_recovery_eval --out /tmp/taskrun-crash.json
pytest -q tests/test_taskrun_process_crash_recovery.py
```

CI 新增短期 Artifact 文件 `agent-harness-synthetic-evidence/taskrun-crash-recovery.json`，包括异常退出注入数、只读任务恢复数、旧租约被拒绝数及负面结论。

## 验收状态与回执完整性

在合成 TaskRun journal 中，第二次执行仍须通过 #111 的服务端 SQL 事实复核，不能只凭 Worker 返回的 SUCCEEDED 字符串判断恢复成功。CI 的 `read_only_tasks_recovered` 仅计数成功写入持久化回执、尝试次数为 2 的任务；所有中止、重试和来源检查都属于本地离线实验，不计真实云模型 Token。

## 严格边界

- 测试确实创建两个真实 OS 子进程，但租约自然到期**没有等待实际时钟**，而是 SQL 故障注入；不能称为真实网络分区或运行时抢占的生产压测。
- 完成结果是从头重新执行只读任务，**不是**原 Deep Agents 思考上下文、规划状态或 token-level checkpoint 恢复。
- 没有模型 API、个人考生信息、外部费用、Java/Redis 分布式任务编排，也没有任意写工具。
- 该失败恢复的观测结果不能外推生产异常恢复率；报表明确 `production_recovery_rate=NOT_MEASURED` 和 `exactly_once_side_effect_guarantee=false`。
