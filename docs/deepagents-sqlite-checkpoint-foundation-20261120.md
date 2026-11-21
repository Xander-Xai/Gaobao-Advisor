# P18｜Deep Agents 可选持久化 Checkpoint：跨进程 LangGraph 状态证明与安全恢复边界

## 已实现（受控实验）

- 可选依赖 `langgraph-checkpoint-sqlite>=3.0.1,<4.0`（最低版本避开早期 SQLite 过滤查询安全问题）。默认 FastAPI、普通 TaskRun 和未配置实验的 Deep Agents 仍不加载该模块。
- 只有当受控 Worker 已获得 TaskRun 授权且显式配置 `SYNTHETIC_DEEPAGENTS_CHECKPOINT_DB=/abs/path/checkpoints.sqlite` 时，Deep Agents 图改用真实 SQLite `SqliteSaver` 保存 LangGraph State/Checkpoint，不再只用当前进程的 `InMemorySaver`。
- 路径必须是存在目录下的绝对路径、不可为符号链接、与招生 SQLite 和 TaskRun journal 分离；数据库文件仅允许拥有者读写（0600）。默认关闭，production 禁用，必须是 SYNTHETIC_DEMO 且 `ENABLE_OUTBOUND_FETCH=false`。
- 新建不可重复绑定表 `synthetic_deepagents_bindings`，将 `task_id` 与虚构画像、固定模型 ID、System Prompt/Skill 契约 SHA256、合成数据集版本绑定。跨进程用同一 `thread_id` 可读到此前的实际 LangGraph checkpoint，换模型或数据契约会拒绝。
- **关键：已有 Deep Agents checkpoint 时拒绝再次自动调用模型**，而不直接将 `agent.invoke(None)` 当作完整恢复。这是主动 fail-closed：原来的工具观察、Evidence Gate 收据仍保留在进程内，盲目恢复可能产生未核验的业务成功回执或重复远程调用。

## 实际跨进程测试（无模型 API）

`tests/test_deepagents_sqlite_checkpoint.py` 使用**真实 LangGraph StateGraph 和真实 SQLite SqliteSaver**：

1. 子进程 A 执行第一个业务图节点，SQLite 写入节点完成状态，第二个节点使用 `os._exit(17)` 模拟进程突然退出。
2. 子进程 B 重新构造同一个图，打开同一个 TaskRun ID 的 SQLite checkpointer，查询 `get_state`，确认只剩第二个节点未执行。
3. 子进程 B 调用 `graph.invoke(None, config)`，完成第二个节点，并检查第一个节点的文件记录只出现一次，**确实没有从头重跑图**。
4. 复核 profile/model/contract 绑定、错误数据源路径、production 拒绝。

```bash
pip install -e ".[dev]" -r requirements-deepagents.txt
pytest -q tests/test_deepagents_sdk_smoke.py tests/test_deepagents_sqlite_checkpoint.py --no-cov --override-ini addopts=''
```

CI 的可选 Deep Agents SDK 作业已包含这项真实跨进程恢复回归；普通 Python 作业没有安装该扩展时会明确 SKIP，不得把 SKIP 当成通过。

## 尚未完成，不可夸大

- **未实现 Deep Agents 的完整恢复**：跨进程持久化 Graph State 目前已具备，但 `observations / seen / receipts` 仍在 `deepagents_adapter.py` 的内存闭包中，尚未实现安全重建、来源重新核验、重复工具调用去重、跨进程继续生成原 Card。
- SDK 的跨进程图恢复实验只证明 LangGraph 底层机制，**不是**外部模型调用/Token 内部重放的实证，更不是模型推理中途精确恢复。
- 不提供并发多主机共享存储、长事务 heartbeat、远程 Provider 硬取消或 exactly-once 外部副作用。
- 这项 SQLite checkpoint 可能保存**完整虚构任务消息和工具输出**。不得用于个人真实资料、私钥、真实招生咨询、或未经加密与访问控制审计的生产数据库。
- 若要开启带模型的正式 checkpoint 续跑，下一步应实现持久化工具观察账本、任务租约 fence 绑定、恢复时工具结果与 SQL Oracle 重核验，并证明已完成工具不会被再次副作用执行。

所有 PR 仍为 Draft、未合并 master。本功能不进行付费 Provider 调用。
