# P19｜Deep Agents 工具观察账本：SQL 回执重建与跨进程合成任务恢复

> 本切片仅适用于 `SYNTHETIC_DEMO` 固定只读 SQL 工具。它不是通用 Coding Agent 的 exactly-once 执行，更不是带真实模型 Token 的性能证明。

## 解决的问题

P18 的实际 SQLite Graph Checkpoint 能恢复 LangGraph 运行图，但原 `deepagents_adapter.py` 在进程内闭包持有 `observations / seen / receipts`。进程异常退出之后即使图节点能够续跑，也无法重建历史工具证据；贸然恢复可能误报业务结果。

P19 为这条**有限的虚构只读证据链路**补齐对应机制：

- 与持久 Graph Checkpoint **共用同一个私有 SQLite 文件、独立表和短事务连接**。业务 TaskRun journal、招生数据 SQLite 仍然是不同文件。始终最多 5 条指定只读 SQL 工具观察和 1 条终态收据。
- 每条工具结果以 `task_id + 冻结 profile/model/Prompt/Skill 契约 SHA + 事件顺序 + 工具名 + 对象 + 内容` 计算 SHA256，禁止重复键、错误对象、额外工具、超预算观察以及更改顺序。
- 恢复进程不是“信任账本中的 JSON”：恢复时用相同虚构参数逐个**重新查询真实合成数据库**，对比当前行、`source_ids` 等字段。如果 SQL 结果、数据版本、工具合同变化，立即拒绝恢复。
- 支持原节点已成功持久化 SQL、但图节点尚未完成时的单次工具重放：第二进程可从账本读取并再验一次旧结果，**不会在账本重复追加另一条观察**。继续重复工具调用仍被拦截。
- `submit_research` 和带负面观察的 `abstain_with_evidence` 也写入持久账本；最终卡片仍须通过 Worker 的独立 SQL 来源复核，再通过 TaskRun 租约 CAS，才能写回业务任务回执。
- 只有已存在且能够还原有效账本的图 Checkpoint 才可 `agent.invoke(None)`；存在 graph checkpoint 却没有任何可核验工具观察、或只存在工具账本而没有图 checkpoint，均 fail-closed。
- 正常 Worker 在每次 Agent SQL/账本写入前检查 `TaskRunStore.assert_active_lease`，取消或租约变化后拒绝继续产生已授权回执。
- 不配置 `SYNTHETIC_DEEPAGENTS_CHECKPOINT_DB` 时，保持原有不可恢复的轻量进程内运行行为；普通 FastAPI 不加载可选 SDK。

## 真实跨进程故障注入与 CI

`tests/test_deepagents_durable_tool_ledger.py` 用**真实 LangGraph + 真实私有 SQLite SqliteSaver + 真实合成 SQL + 假模型图**完成：

1. 子进程 A 调用 2 次只读工具并保存结果，故意在后续节点 `os._exit(17)`；
2. 子进程 B 重建同一个 LangGraph，从已有状态续跑，重新核对 2 条持久观察，再执行剩余 3 条只读查询和来源验证；再次请求已保存工具时只允许一次合法重放；
3. 最终保持 5 条工具观察、1 条终态决定，第一节点只执行一次，返回来源完整、无录取概率的合成证据卡；
4. 独立故障场景：第一进程退出后修改底层合成 SQL 记录，第二进程必须在调用模型前拒绝恢复；
5. SDK 兼容与原先 P18 的真实两进程 Graph Checkpoint 测试继续在 CI 运行。

复现（不产生模型费用）：

```bash
pip install -e ".[dev]" -r requirements-deepagents.txt
python -m pytest -q tests/test_deepagents_durable_tool_ledger.py tests/test_deepagents_sqlite_checkpoint.py --no-cov --override-ini addopts=''
```

## 不宣称的事情

- 真实 Deep Agents 模型在中途退出、远程 API 调用成本和 Token 是否可完整恢复：**未验证**；模型请求发送后但模型响应尚未落盘时，仍可能重新计费。
- SQLite journal 保护不了已经发生的外部写副作用。本项目此切片只开放只读合成 SQL，不提供真实分布式 Exactly Once。
- SHA256 及重新读取 SQL 能发现数据不一致，但**不能对抗同时掌握本机数据库全部写权限的攻击者**；也不是有效真实招生规则 Ground Truth。
- 真实志愿方案人工审核、正式用户付费或招生准确性测试仍未发生。
- 所有依赖 PR 保持 Draft，未合并主分支。
