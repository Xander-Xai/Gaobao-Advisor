# P10｜TaskRun 取消：状态终止与旧 Worker 回执隔离

> 本切片只针对合成 TaskRun。`CANCELLED` 表示后台 journal 不再接受任何该任务的新结果，**不是**已经结束 Python 线程、远程 LLM 请求或容器进程的证明。模型调用可能继续消耗 Token；不将这项工程描述为真正的硬取消或付费中止。

## 已实现
- `SyntheticTaskRunStore.cancel(run_id, principal, admin=False)`：在 `BEGIN IMMEDIATE` 事务中实现所有者/管理员授权、状态机、幂等重放、取消时间更新、租约 token 清理、结果清空与版本递增。允许取消 `PENDING_APPROVAL`、`APPROVED`、`RUNNING`；终态 `SUCCEEDED`、`NEEDS_REVIEW`、`FAILED` 不允许改写。
- `POST /api/v1/admin/agent-taskruns/runs/{run_id}/cancel`：现有 operator/admin 鉴权、默认关闭的合成实验开关，所有者或 admin 可撤销任务。普通 viewer 与匿名用户不能访问。
- 已领取任务取消后，其旧 lease token 失效，旧 Worker 对 `store.finish` 的写入被 CAS 拒绝；随后 `claim` 和租约巡检均不能复活已取消的任务。
- 取消后不再占用所有者的活跃任务配额；不会因取消而删除历史状态。审计记录在 TaskRun 的 `failure_code='cancelled_by_operator'`、版本和时间中；尚没有单独的不可篡改审计事件表。

## 复现
```bash
pytest -q tests/test_taskrun_cancellation.py tests/test_taskrun_store.py tests/test_taskrun_reconciliation.py
```

## 明确剩余缺口
1. Web 请求返回取消确认只能说明**停止交付/拒绝新回执**。如果 Worker 已经进入阻塞式 API 调用，Python 线程及远程计费请求不会被这个接口强制终止。
2. 真正强制取消需受控独立进程/任务队列、运行句柄、Provider 可用的取消 API、强制超时和退出回收；还要测试已发生的非只读副作用与补偿。现有咨询工具只读，不能以此声称一般任务 exactly-once。
3. 仍未完成真实模型多轮 A/B、人工 Ground Truth 审核、真实招生数据授权和实际用户任务交付率。

此 PR 延续 #103→#105→#106→#107→#108→#109→#110→#111→#112→#113 的 Draft stack，暂不对生产路径作变更。
