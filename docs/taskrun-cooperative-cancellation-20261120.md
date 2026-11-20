# P13｜取消感知 Worker：协作式中止与最终回执门禁

> 仅 SYNTHETIC_DEMO，依赖 Draft #116。**本改造不提供 OS 进程强制终止，不能停止已经发送到外部 Provider 的请求或账单**。应用层只提供取消协作点和取消结果不被覆盖的保证。

## 根因与修复

原来 #114 的 `store.cancel()` 在 TaskRun journal 中撤销租约并使旧 Worker 的 `finish()` 失败，但如果 Agent 执行期间任务已取消，Worker 仍会尝试继续进行独立 SQL 收据验证，且异常分支可能继续提交 `FAILED` 并产生不必要的租约冲突。

此次新增：

- `SyntheticTaskRunStore.assert_active_lease(run_id, token)`：基于数据库实际状态、租约截止时间与 token 做只读查询，仅原始 RUNNING 且租约有效时允许继续；不得泄漏租约 token 给网页。
- `execute_approved_run()`：领取后、Agent 返回后、SQL 收据验证后均检查租约。中途取消则跳过后续不必要的处理，并返回已持久化的 `CANCELLED` 安全回执，不输出模型构造的卡片。
- 捕获 `TaskRunConflict` 时只对已 `CANCELLED` 的状态返回取消回执；其他租约丢失或意外错误仍然抛出，避免掩盖真实故障。
- Agent 抛错与取消竞态下，失败回执的 CAS 冲突不会覆盖或泄漏取消前的数据。
- 专门的隔离子进程测试使用**模拟模型 Runner**，覆盖模型返回后取消、模型执行抛错时取消、SQL 验证和最终持久化之间取消、正常完成、租约超时/取消被拒绝，加上真实线程并发取消，共六个场景。**这不是对任何真实模型的验证。**

## 复现

```bash
pytest -q tests/test_taskrun_cooperative_cancellation.py tests/test_taskrun_cancellation.py \
  tests/test_taskrun_process_crash_recovery.py
```

## 没有完成的事项

- 真正向 Provider 发送取消请求、终止工作子进程以及资源清理；当前代码不能保证花费立即停止，也无法对执行中阻塞 Python 线程施加抢占式终止。
- 模型内部 checkpoint 继续运行、分布式任务高可用、真实招生数据授权、人工 Ground Truth、真实 Token/成本 A/B 实测。
- 合成研究任务的工具都是只读 SQL；取消不构成通用工作流外部副作用的补偿或 exactly-once 证明。
