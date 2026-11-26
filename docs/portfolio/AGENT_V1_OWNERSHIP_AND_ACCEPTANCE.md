# Agent 工程标准 V1.0 — 高考顾问项目责任边界与验收台账

> **历史工程/职业证据快照**，不是当前项目状态页。当前实现和验收请看 [PROJECT_STATUS.md](../PROJECT_STATUS.md)；近期来源治理和 Chat 研究集成见[工业招生证据治理](../architecture/industrial-admissions-evidence.md)。

> 该台账的基线快照为 2026-11-25；其中阶段性未合并状态不代表当前分支。任何招聘或任职陈述仍需独立核实，不能将本仓后续公开提交追溯为历史雇主生产交付。

## 求职项目定位、任职时间及代码归属

- **唯一主责**：另一段更近期任职经历的业务全栈交付与 Agent Harness/Eval 实验：Vue/FastAPI/SSE、结构化查询优先、证据核验、只读受限 Agent Loop、模型评测、上下文预算、成本与发布治理。
- 与前一任职阶段的 `Customer-Service-AI-Agent`、`Beauty-Industry-RAG-QA-System` **不可混写技术交付时间**。尤其 `server/agent/` 与 `benchmarks/` 中的近期公开仓库增量，不能自动声称已在当时企业环境上线。
- 此仓从早期 MIT 许可上游代码继续迭代，不应把全部原始代码说成从零原创；保留 `THIRD_PARTY_NOTICES.md` 的归属与数据边界。真实学校数据、真实考生效果、付费 Provider 成本均未由本仓验证。

## V1.0 模块责任

| 模块 | 本仓责任 | 已有入口 | 当前证据边界 |
| --- | --- | --- | --- |
| M01/M02/M05 | Vue3/FastAPI/SSE、SQL/检索、LangGraph 确定性 12 节点在线准备图 + SSE 唯一收尾；17 节点参考图 | `server/` `frontend/` | 合成业务 E2E；不将节点数当模型自主工具步数 |
| M03/M04/M09 | 独立只读 Harness、Planner → Tool → Observation、预算与 TaskRun | `server/agent/readonly_harness.py` `server/agent/taskrun_store.py` | 有脚本化合成验收；真实模型完成率未测，Deep Agents 非默认在线业务 |
| M07 | Context 预算、来源保护、Skills、可选持久证据记忆 | `server/agent/context_policy.py` `server/agent/governed_evidence_memory.py` `server/agent/skills/` | 部分实现；动态工具渐进披露、Prompt Caching、负向记忆与长期对照未完整验收 |
| M13（主责） | 合成业务独立 SQL Oracle、模型运行对照、Bad Case、Holdout | `benchmarks/decision_card_business_eval.py` `benchmarks/model_run_comparison.py` | 8 个冻结合成任务；真实模型 Provider 执行、人工真值与统计稳健性未验证 |
| M14/M16（实验） | TaskRun Trace、评测计费归因、部署与前后端回归 | `server/observability/` `benchmarks/provider_cost_reconciliation.py` `docker-compose.yml` | 成本估价不是付款凭证；未形成生产灰度/真实 Provider 性能 |
| M11（沙箱） | 受审批招生研究的受限分析容器；合成 Coding fixture 仅回归 | `server/agent/admissions_analysis_sandbox.py`、`server/agent/sandbox_runtime.py`、`benchmarks/sandbox_workspace.py` | 真实授权数据、容器部署和多租户安全未验收 |

**明确不做**：把高考顾问重构为 9 Agent 客服系统；重复创建企业级退款 Ledger 或完整多模态 RAG 检索服务。真实业务事实优先 SQL，政策类文字走检索；模型只在明确许可的局部范围自主决策。

## 架构收敛后的证据范围

#192/#193 已合入；在线咨询、原生 Harness 基线与复杂研究职责分离，私有 operator journal 是批准研究的单一生命周期。Deep Agents 使用 InMemorySaver；私有任务恢复不等于跨进程图恢复。受限招生 Sandbox 已有业务链路，算术修复仅保留为合成安全夹具。已合入的授权研究 Eval 支持独立金标和私有回执（`benchmarks/authorized_research_eval.py`）；#194 已合并（`9a93b30c35cbf7478e6d342115e34086ef8837ac`），同一 journal 的聚合评测扩展；合并提交 CI 需独立核对。后续 #196 已合并，修复委派回执完整性与独立 no-history 拒绝原因。真实数据许可、独立金标、实际模型/账单及同模型 A/B 均继续保持 OPEN。

## 第一批补全任务

| 编号 | 执行项 | 通过标准 | 当前状态 / 关联变更 |
| --- | --- | --- | --- |
| GB-P0-01 | 真实模型受约束多步业务 Eval | 固定 8 合成任务 + 扩大独立 Holdout；模型版实跑、独立 SQL Oracle、正确拒答、轨迹、provider token 与失败分类 | **PARTIAL**；可选模型执行接口已实现，仍未完成真实模型结果 |
| GB-P0-02 | 失败归因与任务覆盖审计 | 缺失案例/非法 identity/重复样本/Token 未知/意外拒答均被报告；不得把 scripted CI 当真实 LLM | **MERGED**：#143 已合入；真实模型与数据仍待验收 |
| GB-P0-03 | Context / Prompt / Skills 有效性 A/B | 同模型/同任务/同工具和价格快照，候选改动与基线分开保存；质量安全不退化后才能讨论 token 变化 | **PARTIAL**；既有实验工具，真实重复运行和独立 Holdout 仍缺 |
| GB-P0-04 | 决策卡面向用户的业务质量闭环 | 关键事实必须来自 SQL，缺失数据拒答；真实模型回复与最终展示卡逐字段交叉验证，合成环境先验 | **PARTIAL**；隔离运营侧决策卡不等于公共 chat 已由模型自主编排 |
| GB-P1-05 | Trace/评测回放/单任务 FinOps | 一条任务可关联 Trace、工具序列、状态、模型用量与真实账单，对照失败 Bad Case | **PARTIAL**；Provider 账单与端到端使用成本未验收 |
| GB-P1-06 | Skills 渐进式披露/负向记忆 | 合成只读任务中按需加载、记录审核拒绝规则并保证跨任务引用权限；A/B 验证无泄露和无重复错误 | **REFERENCE_IMPLEMENTED**：`skills/service.py::build_disclosed_context`、`server/agent/synthetic_negative_rule_memory.py`、`server/agent/synthetic_negative_context.py` 与对应单测（允许名单规则、异人审核、TTL、按 owner 隔离、审计、跨实例 Context 组装）；均为 opt-in 合成实验，**尚未接入线上路径/真实 A/B/可信 TaskRun provenance**，不等于生产个人记忆 |
| GB-P1-07 | 可选沙箱及灰度发布 | 攻击测试无越权挂载/网络/凭证；真实模型灰度有退出和回滚证据 | **PARTIAL**；隔离 CLI 实验不等于线上灰度 |

## 当前可执行验证

```bash
pytest -q tests/test_skill_disclosure.py tests/test_synthetic_negative_rule_memory.py tests/test_synthetic_negative_context.py --no-cov -o addopts=''
pytest -q tests/test_readonly_harness.py tests/test_decision_card_business_eval.py
python -m benchmarks.readonly_harness_eval --out /tmp/gb-readonly-eval.json
python -m benchmarks.decision_card_business_eval --out /tmp/gb-decision-baseline.json
pytest -q tests/test_portfolio_taskrun_http_e2e.py
```

以上全部是默认无 Provider 费用的合成验证，**不**输出真实模型完成率或真实招生准确率。真实模型 API 仅在获授权、配置限额与支付预算后显式试验；需核验模型版本、provider usage、签署金标/SQL Oracle、失败样本、代码与数据摘要。不得靠增加几十个 synthetic tests 伪造业务增长。

## 发布与简历约束

- 项目基础工作和后续开源增量分别标记时间、本人贡献与验收范围；后续公开改造不得自动计为历史任职成果。
- 用 `IMPLEMENTED / SCRIPTED_EVAL / VERIFIED_LOCAL / REAL_MODEL_VERIFIED / PRODUCTION_VERIFIED` 区分证据；Master、Draft PR 不混合。
- 该项目负责 **Harness + Eval + 全栈交付**；客服负责企业级 Runtime 和真实业务写操作；化妆品 RAG 负责检索算法深度。

参考：`README.md`、`docs/model-run-paired-evidence-20261120.md`、`docs/agent-readonly-harness-evidence-20261120.md`、`THIRD_PARTY_NOTICES.md`。


### Harness 优化审核绝对护栏（2026-11-24 增量）

`benchmarks/harness_optimizer.py::compare_holdout` 除逐案例非回退和成本改善外，
要求候选在整个已执行的冻结配对集上没有已报告的安全违规，并且至少有一个 holdout
任务通过独立事实与任务评分。持续存在的违规和零成功任务的 Token 节约均不能进入优化审核。
这只是最低审核门槛，不是质量达标、统计显著性或上线许可；真实模型调用及独立安全评审仍待验收。
复核：`pytest -q tests/test_harness_optimizer.py`。关联 #104、#147。


### 真实只读 Harness Eval 执行前预检

`python -m benchmarks.readonly_model_eval --model <固定模型版本> --reviewed-dataset <人工审定文件> --out /tmp/gb-plan.json --plan-only` 不需密钥或网络，输出计划分母、Holdout 数、冻结选择哈希和预算，不调用 Provider。审定集默认执行全量（当前 20 条，含 5 条 Holdout）；显式截断标记 REVIEW_DECLARED_SUBSET，不进入优化审核。未审定集默认 5 条探索性用例。实际执行移除 --plan-only，并在授权费用后添加 --permit-external-model，使用不同输出路径；原报告不得覆盖。此计划不代表人工审定真实完成、模型质量或账单。


### 模型非法响应与真实调用失败的计量边界

只读 Harness 收到无法解析的 JSON、非法 action 或错误的工具字段类型时，
记录固定失败码 `planner_response_invalid`（不执行工具、不记录原始输出），保留
Provider 返回的有效 usage。该样本仍为失败；评测可在剩余预算内继续后续案例。
没有响应或 usage 不可用时保持未知并停止真实评测套件。预算仍为响应后计量，
不是硬性账单上限。此修复不改变默认在线聊天链路，真实 Provider 结果仍待实测。

复核：`pytest -q tests/test_readonly_harness.py tests/test_readonly_eval_suite_budget.py`。
关联 #104、#147。


### 原生 Harness × 审批 TaskRun（实验增量）

管理端可创建 `native_harness` 合成决策卡任务，仍须不同管理员审批。HTTP execute 拒绝模型引擎；
授权操作员可显式运行：

```bash
python -m server.agent.taskrun_worker --journal /absolute/path/tasks.sqlite --run-id syn_... \
  --model openai:MODEL_ID --permit-external-model
```

需要显式非生产 APP_ENV、合成 SQLite、禁用 outbound fetch、实验开关及 Provider Key。
原生模型 Planner 仅在 Worker 中选择固定画像下的五个只读查询；执行前检查任务租约，
每次模型调用及 SQL 工具前再次检查；拒绝模型版本漂移、缺失身份和无法计量的结果。
独立 SQL 回执复核后才持久化，管理端可刷新查看。Provider 用量为响应后计量，非硬账单上限；
取消不能撤销在途请求，丢失租约后禁止新模型调用/新 SQL 操作及发布。
此为管理端合成任务路径，不改变公开聊天，不声称真实模型/真实招生效果。
关联 #104、#147；回归 `tests/test_native_taskrun_harness.py` 和 `tests/test_agent_taskrun_api.py`。


## 2026-11-25 实证冲刺门禁（#104 / #147）

按顺序执行，不再增加并行 Harness、评测框架或公共 Agent 入口。每次运行记录代码 SHA、数据/金标摘要、执行入口、环境、模型实际版本、预算、完整分母及失败类型；不同证据层不得混算。

| 阶段 | 验收执行 | 通过条件 |
| --- | --- | --- |
| E0 无 Key 基线 | 现有 `readonly_harness_eval`、`decision_card_business_eval`、`context_ab_eval --scripted-smoke`、授权研究 Eval fixture 回归 | 冻结合成数据、逐样本独立 SQL 真值、错误输入与缺失数据拒答；仅 SCRIPTED_EVAL |
| E1 真实模型合成业务 | `benchmarks.decision_card_business_eval` 固定 8 案例，4 development + 4 holdout；先完成预算预检 | 逐案例记录正确事实、工具选择/参数、合理拒答、失败归因、用量和耗时；不能用确定性零 Token 基线证明降本 |
| E2 Context 单变量 A/B | `benchmarks.context_ab_eval`；同版本模型、相同数据/工具/预算、至少 2 组配对试验 | 每个 holdout 保留硬约束及证据，质量安全不退化；仅在完整真实 usage 存在时报告 Token 差异；样本少不宣称显著性 |
| E3 授权复杂研究 | operator 创建任务、独立管理员审批、同一私有 journal + Deep Agents + Sandbox；`benchmarks.authorized_research_eval` | 冻结独立 gold、完整任务绑定、可信回执与聚合判分；通用 FAILED/BLOCKED 不计正确拒答；独立原因 oracle 缺失则保持 OPEN |
| E4 全栈验收 | 已有 Compose E2E、Java Services 与 API/SSE 测试；对照真实启动环境 | 正式 Java Gateway/Catalog、MySQL、Redis 与独立 Worker；Python 唯一 chat/SSE 入口；鉴权、限流、断流及依赖不可用保留明确失败状态 |

建议先执行以下不花费 Provider 费用的命令（输出新目录；不得覆盖历史结果）：

```bash
python -m benchmarks.readonly_harness_eval --out /tmp/gb-readonly-new.json
python -m benchmarks.decision_card_business_eval --out /tmp/gb-decision-new.json
python -m benchmarks.context_ab_eval --scripted-smoke --out-dir /tmp/gb-context-new
pytest -q tests/test_authorized_research_eval.py tests/test_context_ab_eval.py
```

真实模型合成业务的配对预算预检（默认不执行网络调用）：

```bash
python -m benchmarks.repeated_model_experiment --model YOUR_VERSIONED_MODEL \
  --out-dir /tmp/gb-paired-new --trials 2 \
  --max-provider-calls-per-arm 64 --stop-after-reported-tokens-per-arm 120000
```

以上数值仅为预检示例，不是授权预算。付费执行需操作员确认 provider、模型版本、总预算、专用凭据和网络许可；响应后 Token stop 不是硬货币上限。真实招生数据另需合法授权与独立审查。具体运行契约见 `docs/model-run-paired-evidence-20261120.md` 和 `docs/validation/authorized-research-eval.md`。

### 面试量化证据口径

- Task Success = 独立业务真值通过任务 / 全部计划任务；另列未执行、基础设施失败、模型失败和安全拒答，不能只按成功返回分母。
- 正确拒答需独立业务原因和证据，不得将超时/服务不可用算作正确；有独立标注才计算拒答 precision/recall。
- Tool correctness 同时检查工具名、参数、权限与来源；不可仅用工具调用次数替代正确率。
- Context A/B 比较同模型两臂的完整配对任务、总 input/output Tokens、逐样本安全/事实差异、耗时；未知 usage 保持 null，未完成试验不能报告改善百分比。
- 端到端延迟记录计时边界、样本数、失败、P50/P95；SSE TTFT 与研究任务总耗时分开。少量样本仅作探索证据。
- 成本/成功任务需核对实际价格及独立账单，计入失败请求；零成功或未知成本不计算。无真实值标记 NOT_MEASURED。

### 现阶段三个架构深挖问题

1. **确定性事实 vs 模型推理**：公共咨询 SQL/规则筛选及证据 gate，复杂研究才启用 Deep Agents。展示选科冲突、缺位次、数据版本不符时拒答，而不是给模型编造概率。
2. **长对话与 Context**：区分当前硬约束、SQL/RAG 证据、短期历史摘要与显式用户偏好；压缩不截断当前证据，偏好可撤回，不把摘要当事实。真实收益仍由 E2 验证。
3. **跨语言部署**：Java Gateway/Catalog 只读 overlay，Nginx 将 chat/SSE 留在 Python；演示鉴权、429/503、路由归属和数据一致性，不能声称 Java 接管 Agent 或生产 SLA 已验证。

本次新增的是验收执行协议，不是实际模型结果。真实模型、招生数据、独立签署 gold、真实账单和生产部署仍由 #104/#147 跟踪；#190 保留本地独立 smoke 缺口。


## 工业发布责任

正式功能：确定性咨询、SQL 事实、运营质检与审批后的复杂招生研究。原生 Harness、合成决策卡、负向记忆和编码沙箱仅作隔离回归，不冒充产品功能。标准本地全栈启用加密 Session Snapshot 和 Worker 清理；工业候选仅 SQL 数据路径，未经隐私/授权验收的功能默认关闭。以 [工业发布合同](../architecture/industrial-release-contract.md) 为准，当前不能宣称全功能生产通过。
