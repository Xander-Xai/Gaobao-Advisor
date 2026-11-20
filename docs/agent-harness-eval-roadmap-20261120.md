# Gaobao-Advisor → 可评测 Agent 工程项目：实施与简历证据门禁（2026-11-20）

> 状态：候选实施方案；本分支已提交 **P0 静态 Trace 评分契约、单元测试与 P1 最小 3 条合成 Graph 回放入口**；仍缺正式规模化 Eval、完整故障注入与模型 Token 指标。不得把规划中的能力写进投递简历。默认 SYNTHETIC_DEMO，非真实高考推荐产品。

## 1. 为什么不直接把 LangGraph 换成 Claude Code / Pi

现有高考志愿辅助决策是受业务规则约束的咨询系统：考生画像、SQL、BM25 合成语料 RAG、来源标注、风险提示与 SSE。政策引用、数据版本、权限与错误处理应保持确定性；动态规划只适合低风险、只读的探索式任务。项目最好的改造方式是保留 LangGraph 业务控制层，先补齐 Trace→Eval→归因→回归的证据链，再在**受限只读工具集合**中验证自主 Agent Loop。不要为了简历而创造没有业务价值的沙箱 Shell 能力。

## 2. 北极星与观测口径

- **Demo 级北极星（待 P1 实测）**：合成志愿咨询用例中，符合**预先定义的业务任务规格**且正确标识来源/风险/降级状态的任务比例。任务规格由测试数据与规则校验定义，不让 Agent 自评即算正确。
- **护栏**：无证据回答、跨会话污染、伪造来源、未授权数据访问、故障时 fail-open 都必须作为阻断。
- **Node steps ≠ Tool calls**：当前 LangGraph `trace[*].node` 是图节点访问量，不是独立工具调用数；不能拿来宣称“工具步长改善”。
- **Token/成本**：当前无统一模型 usage 采集与价格快照，不报告 Token 节省或成本下降。
- **业务结果**：公开数据均为虚构合成数据，不能验证真实推荐准确率、真实考生满意度或招生结果。

## 3. 阶段计划与验收

### P0（本 PR，低风险基础切片）：可机器评分的 Trace 契约

- 新增 `benchmarks/agent_eval_contract.py`：对已有 Graph `state.trace` 的**观测结果**使用显式 rubric 评分。
- 可检查 `scene`、节点到达、槽位缺失、注入拦截、RAG 状态/证据、结构化卡片，以及内存持久化失败。
- 输出只允许 case ID、节点名、错误代码和聚合计数；**不输出用户输入、对话、槽位、来源全文或异常文本**。
- `summarize` 明确 `tool_call_count/token_usage/end_to_end_task_success/real_admissions_quality = NOT_MEASURED`。
- 契约单测验证正常/负控制/隐私/失败/指标边界。单测不等于真实 Graph 全链路 Eval。
- **验收**：`pytest tests/test_agent_eval_contract.py -q --override-ini addopts=''`，且目标仓 CI 全绿。实际 CI 以 PR 检查为准。

### P1（3 条 smoke 回放入口已实现，规模化验收待开发）：真实 Graph 离线回放 + 基线数据集

- 目前已提供 `python -m benchmarks.agent_eval_replay --out /tmp/gaobao-agent-eval.json`，采用临时 SQLite、拦截出站 socket、只输出白名单评分字段；**真实 Graph 回放的成功率尚未经本轮全链路运行验证**。
- **严格隔离数据库**：固定合成种子，仅在单独 SQLite 临时路径运行，禁止连接现有 MySQL/生产库；无网络且无模型 API Key 的 CI Job。
- 先建立最小 30 条人工验收规范（画像/SQL、知识/引用、注入/拒答、检索损坏、续聊）；每条包括输入、可验证期望、失败标签，避免合成“标准答案”未经审核就当真值。
- 通过现有 `build_advisor_graph().invoke` 获取**真实 Trace**，使用 P0 契约自动评分，保存无敏感字段的 JSON 结果、运行配置与 commit SHA。
- **验收**：可复现基线，独立执行、人工复查负样本；校验无用户原始消息泄漏；不能用纯单测通过率代替模型/业务质量。

### P2（待开发）：结构化 Trace 与 Runtime 鲁棒性

- 对 Graph、SSE、检索、SQL 增加 request/run correlation id、span、耗时、错误分类（脱敏）及调用边界；采集真实工具调用计数与模型 usage，若 provider 无 usage 则明确 UNKNOWN。
- 失败注入：RAG 不可用、SQL 超时、SSE 客户端断开、内存写入失败、重复请求；建立有界重试、终止条件和 fail-closed 回归。
- **验收**：失败分型可归因，节点/工具/API 维度分离，故障恢复效果基于证据记录，不预设成功率数字。

### P3（待开发且可选）：受约束只读 Agent Loop / Deep Agents 对照

- 在已有业务控制图内扩展一个**只读工具探索子流程**（查询院校/专业、查询分省记录、BM25 检索），显式工具 Schema、allowlist、步数与 Token 预算、无效循环检测。
- 对比固定图方案与 Deep Agents/自研 Loop 的相同评测集，选择业务质量、时延、成本与可靠性更合适的一方。
- 禁止模型直接执行数据库修改、外网抓取、宿主机命令、自动填报志愿；对需要执行代码的实验单独设计隔离环境，别借“Sandbox”概念给业务增加不必要风险。
- **验收**：真实调用轨迹、失败恢复、工具步数及成本可测；结果不达标就保持当前 LangGraph 流程。

## 4. 简历 Claim Gate

| 能力 | 当前可写 | 不允许写 |
|---|---|---|
| FastAPI/Vue + LangGraph + BM25 | 已有公开工程，单图 17 节点、合成数据 RAG、SSE | 自研通用多 Agent 平台、真实高考检索质量 |
| MySQL/Redis/Java | 可选 Java Gateway/Catalog overlay + Compose 集成验收 | 默认线上微服务、高可用生产部署 |
| Trace | 图节点 Trace 已存在；P0 新增评分契约（仅 PR，未合并） | 完整分布式 Trace、Token/工具成本优化已完成 |
| Eval | P0 契约单测成功后可说明“实现离线 Trace 评分模块” | 真实业务 Eval/Sandbox/半自主优化已完成 |
| 来源 | 基于上游 MIT 时代代码二次开发并加固 | 从零原创、完整企业级商业化产品 |

## 5. 岗位表达

- **应用/全栈**：真实业务链路 + Python/Java/MySQL/Redis + SSE + 权限 + CI/Compose + 故障处理。
- **Agent Infra/Harness**：状态图、执行轨迹、恢复与拒答机制；只有 P1/P2 落地验收后才能增加“自动化回放评测、Token/工具步长、故障归因”等更强表述。

**对外简历真实性优先级高于术语覆盖率。**
