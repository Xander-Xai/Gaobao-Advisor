# 2026 Agent 求职定位：Harness、业务 Eval 与 AI 全栈（最近任职阶段）

> **文档性质**：求职工程证据与验收路线，不是雇主官方代码或生产证明。仓库所有者表示本项目属于与 Beauty/Customer Service 不同的、较新的任职阶段；本仓库是基于上游 MIT 许可代码的公开二次开发，不能推导后续所有公开改动都发生于任职期间。当前功能和历史贡献必须分别核实。

## 唯一主线

**业务决策可信性 + 受控 Planner/Tool/Observation 循环 + Eval/Context + Vue/FastAPI/SQL 全栈交付**。本项目不重复九角色客服 Agent，也不重复多模态检索底座。

## 已实现和必须区分的边界

| 能力 | 代码依据 | 可宣称边界 |
|---|---|---|
| 高考辅助问答业务路径 | `server/graph/`, `server/routes/`, `frontend/src/` | 17 节点单图、SQL + 合成 BM25 + SSE；不是九 Agent 系统 |
| 独立只读 Harness | `server/agent/readonly_harness.py`, `readonly_tools.py` | 受限 Loop 和工具执行，**不在默认在线 chat 路由** |
| 可选模型自主工具选择 | `server/agent/decision_card_model_planner.py` | 真实模型适配器存在，但实际 provider 任务成功率尚未测量 |
| 业务真值 | `server/agent/portfolio_sql_oracle.py`, `benchmarks/decision_card_business_eval.py` | SQLite 合成事实的独立评分，不等于真实高考推荐效果 |
| Checkpoint/TaskRun/隔离实验 | `server/agent/taskrun_*.py`, `synthetic_checkpoint.py`, `benchmarks/sandbox_workspace.py` | 局部工程机制的合成/本地证据，不能说生产多租户沙箱 |
| 全栈组件 | `server/main.py`, `frontend/`, `java-services/`, `docker-compose*.yml` | Java、MySQL、Redis 均需讲清可选 profile 与验收环境 |

## 缺口责任与执行顺序（只在本仓库处理）

1. **P0 — 真实模型业务任务闭环（Issue #104）**：固定 8 条合成案例与独立 SQL 真值，显式人工批准 API 预算后执行真实模型 Eval；记录模型 ID、温度、Prompt/工具版本、完整脱敏 Trace、provider Token、时延、失败与 `run_id`。未授权调用时维持 `NOT_MEASURED`。
2. **P0 — 真实成功率与拒答准确性**：开发集和冻结 Holdout 严格隔离；一律拒答不能算成功。无真实招生授权/专家金标时，只能报告 **synthetic task success**，不说录取建议准确率。
3. **P0 — 优化对照（PR #143 为已有切片）**：同一个真实模型、同一业务案例、同一 SQL Oracle、同预算的 Prompt/Skill/Context A/B，对比完成率、任务成本、工具步数、质量与失败；不能拿规则控制器的零 Token 与真实模型比较为“Token 降低”。
4. **P1 — Replan、Context 和长期记忆**：只对真实失败模式添加 Replan/Context 改进，保留来源、身份边界、预算、撤回/失效和跨任务隐私；评测后才考虑合入。现有堆叠 Draft PR #132–#138 按依赖链与安全审查执行，禁止把 Draft 视为主线。
5. **P1 — 全栈端到端**：Vue → API → SQL/知识 → SSE/卡片 → trace → 用户可核验结果；两种非法权限/数据缺失拒答；独立 MySQL + Redis + Java overlay 可选，不强制把可选组件全开。
6. **P2 — 新生态**：只在场景确有需要时对接 Deep Agents/Skills/MCP；受限 Workspace 不等于 OS/多租户生产 sandbox，不能凭技术标签升格证据。

## 面试最小验收包

- 单图业务链路与隔离实验 Harness 的对比图：说明为什么生产端仍保持确定性约束。
- 五工具合成决策卡的完整工具轨迹；一条数据缺失、一条违反选科约束的拒答轨迹。
- 真实模型执行的开发/Holdout 分开报表与独立 SQL Oracle 校验；无授权运行则明确标 `NOT_MEASURED`。
- 一条修改 Prompt/工具规则后的配对 A/B，输出有效增益和代价，保留失败案例、不以自评替代真值。
- Vue + FastAPI + 数据服务可操作演示，区分合成数据和现实志愿建议。
- 代码归属、上游 MIT 许可和个人增量贡献如实交代；公开维护 commit 不能代替雇主项目原始证据。

## 跨项目分工

- [Customer Service Agent](https://github.com/Xander-Xai/Customer-Service-AI-Agent) 负责较早任职阶段的多 Agent Runtime/HITL/外部业务工具可靠性。
- [Beauty RAG](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System) 负责较早任职阶段的真实多模态知识检索与证据链。
- 本项目重点回答 2026 年岗位对 **Planner/Harness、Agent Eval、Context、全栈交付** 的要求，避免三个仓库内容膨胀重叠。

## Local Agent portfolio profile (2026-11-24)

Default `docker compose up` enables the existing synthetic operator experiments and a separate persistent TaskRun journal. Configure admin credentials as documented in `.env.example` before login; anonymous users do not gain operator access. The raw no-env FastAPI runtime remains opt-in. Production is blocked by the existing environment gate. This is not the default public chat model Harness (approved synthetic Native Harness TaskRun was merged in PR #172).

Local Compose binds all published ports to loopback and defaults `USER_MEMORY_ENABLED=true` and `GAOBAO_CHAT_COMPACTION_ENABLED=true`. Preference storage still requires an explicit vault/write and bearer token; there is no automatic capture. If a model is configured, long-history compaction may perform an additional provider call; use the explicit false override if this is not desired. `GAOBAO_CHAT_SNAPSHOT_ENABLED` remains false until a private fictional-only run explicitly opts in, because it persists plaintext dialogue. Never use this profile with real personal data. Use the separate production Compose for deployment; existing production memory guards remain unchanged. Sandbox remains a CLI-only isolated experiment, not an arbitrary execution API.

Acceptance: start local Compose after configuring operator/admin authentication; verify unauthenticated requests fail and operator example endpoint succeeds; create a synthetic task and approve with a different authorized admin; verify production rejects experiments and explicit false disables them. Run existing API/TaskRun/memory tests and exact-head CI. No new runtime results are claimed.

The optional MySQL/Redis full Compose overlay keeps synthetic experiments, cross-task memory and compaction **off** unless explicitly enabled via shell or `--env-file .env.full`. Run the full overlay with `docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml --profile full up --build`. Compose interpolation and service env_file are distinct. The existing full Compose CI script now renders both profiles, checks loopback bindings, local defaults, explicit false rollback, full default-off and explicit opt-in without logging resolved credentials. This is configuration evidence only, not a model or privacy production acceptance.
