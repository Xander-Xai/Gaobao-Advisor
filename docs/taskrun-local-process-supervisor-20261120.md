# P20｜真实本地进程取消：受控 Supervisor + 进程组回收

> 仅受控 `SYNTHETIC_DEMO`，默认关闭。**可真实终止本机 Worker 及其同进程组子进程；不意味着远端 LLM Provider 接受取消，更不承诺退款或零 Token 费用。** 当前只在 POSIX 环境实现，Windows 和跨容器/跨主机不属于此切片。

## 先前问题

P13 的 `POST /api/v1/admin/agent-taskruns/runs/{run_id}/cancel` 在 SQLite TaskRun journal 中撤销租约、阻止 Worker 提交成功回执，但若 Agent 已进入阻塞模型请求或工具，**执行 OS 进程仍然存在**。

## 本次实现

- 新模块 `server/agent/taskrun_supervisor.py` 采用固定 `sys.executable -m server.agent.taskrun_worker --journal ... --run-id ...`，不接受用户提供的 PID、命令、Shell、cwd、信号目标或可执行文件。
- Supervisor 只使用 `subprocess.Popen(start_new_session=True)` 创建自己的本地 worker 进程组；以有上限的 0.01–1 秒间隔读取已有 TaskRun journal。真实 Owner/Admin 原取消 API 持久化 `CANCELLED` 后，Supervisor 发送 `SIGTERM`，短暂宽限后向**自己创建的组**发送 `SIGKILL`，清理该组内的子进程。
- 执行时间上限与终止等待上限严格校验；到达硬超时终止本地进程，但**不伪造**业务已 CANCELLED/FAILED。业务仍需依据现有 lease expiry 进入 TaskRun 重试/恢复。
- 普通 Worker 退出后，Supervisor 只信任持久化数据库中的最终状态；不依靠进程退出码判断业务成功。
- 完整 process tree 运行时不将模型原文、工具 stdout/stderr 写到 HTTP 响应或应用日志；只有固定枚举的 Supervisor 结果、退出码、是否触发本地信号以及是否需要 TaskRun lease recovery。
- 增加操作员本地命令：`python -m server.agent.taskrun_supervisor --journal /absolute/path/taskruns.sqlite --run-id syn_<id>`。
- **FastAPI opt-in**：`SYNTHETIC_TASKRUN_USE_PROCESS_SUPERVISOR=true` 时，原有 `POST /runs/{id}/execute` 仅对原先允许的 `deterministic/portfolio` 通过固定 OS 子进程执行。原有 Owner/Admin `POST /runs/{id}/cancel` 不变；内部 Supervisor 观察到取消后真正发送进程组信号。使用 12 秒本地硬上限，短于原有 15 秒 HTTP 响应上限。默认仍使用旧的线程版本，不擅自改变部署行为。
- Deep Agents 命令行只有由**本地操作员同时明确提供** `--model` 与 `--permit-external-model`、且已有合法 Provider key/实验开关时才可以启动模型；HTTP 永不能发起收费 Deep Agents 请求。测试环境没有真实 Provider key。

## 实际验收

```bash
pytest -q tests/test_taskrun_process_supervisor.py tests/test_taskrun_supervisor_http_e2e.py
```

覆盖：
1. 真正子进程 `store.claim` 进入 RUNNING 后，在另一线程用 owner 身份取消；Supervisor 实际终止 Worker 与同组孙进程，迟到的孙进程文件回执不会出现，TaskRun 保持 CANCELLED、无模型结果。
2. Supervisor 硬超时也终止 Worker/孙进程，但数据库状态不被伪造成“用户取消”，明确 `requires_lease_recovery=true`。
3. 真实 CLI Worker 对虚构 SQLite SQL 正常执行完成；Supervisor 回执与 journal 的 SUCCEEDED/NEEDS_REVIEW 一致。
4. 直接调用生产模式拒绝、超时预算非法拒绝、未授权 Deep Agents 参数拒绝、非 APPROVED 任务拒绝。
5. 完整 FastAPI TestClient HTTP 中开启 Supervisor opt-in，验证操作员创建、管理员审批、HTTP 执行成功，以及并发 HTTP 执行中调用**原有取消端点**确实中止本地 Worker 的 OS 进程树；生产模式仍拒绝端点。

## 边界与待做

- 如果远端 Provider 已接收请求，**本地杀进程不等于远端取消、停止推理或者停止计费**；Provider 的取消 API 与收费回执还需要具体接入和实测。
- 只对 Supervisor **自己创建**的本地进程组负责；已有非受控 worker、其他服务/Pod、逃逸进程组的子进程或 Supervisor 自身遭到 SIGKILL 的情况不保证回收。
- 该进程组措施不是 Docker / seccomp / cgroups 多租户安全沙箱，也不保护外部副作用 exactly-once。
- HTTP 默认仍使用原有线程执行路径，因此必须显式开启本地 Supervisor 才具备 OS 硬取消；服务部署参数和可靠外部进程管理还需要灰度验证。
- 真实模型付费多轮 A/B、人工审核 Ground Truth、合法真实招生数据、真实志愿方案交付、生产高可用仍是独立未完成项；不应由本 PR 的合成测试数字替代。

所有增量 PR 继续保持 Draft，未合并 `master`。
