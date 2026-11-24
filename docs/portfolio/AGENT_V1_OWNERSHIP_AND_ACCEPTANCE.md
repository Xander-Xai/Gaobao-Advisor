# Agent 工程标准 V1.0 — 高考顾问项目责任边界与验收台账

> 状态：执行台账，不是模型效果或真实招生业务验收报告。基线：`master` @ `4b7e1cb13368bedb4797096f8cce139a1959885e`（2026-11-24 核查）。

## 求职项目定位、任职时间及代码归属

- **唯一主责**：另一段更近期任职经历的业务全栈交付与 Agent Harness/Eval 实验：Vue/FastAPI/SSE、结构化查询优先、证据核验、只读受限 Agent Loop、模型评测、上下文预算、成本与发布治理。
- 与前一任职阶段的 `Customer-Service-AI-Agent`、`Beauty-Industry-RAG-QA-System` **不可混写技术交付时间**。尤其 `server/agent/` 与 `benchmarks/` 中的近期公开仓库增量，不能自动声称已在当时企业环境上线。
- 此仓从早期 MIT 许可上游代码继续迭代，不应把全部原始代码说成从零原创；保留 `THIRD_PARTY_NOTICES.md` 的归属与数据边界。真实学校数据、真实考生效果、付费 Provider 成本均未由本仓验证。

## V1.0 模块责任

| 模块 | 本仓责任 | 已有入口 | 当前证据边界 |
| --- | --- | --- | --- |
| M01/M02/M05 | Vue3/FastAPI/SSE、SQL/检索、LangGraph 确定性 17 节点业务图 | `server/` `frontend/` | 合成业务 E2E；不将节点数当模型自主工具步数 |
| M03/M04/M09 | 独立只读 Harness、Planner → Tool → Observation、预算与 TaskRun | `server/agent/readonly_harness.py` `server/agent/taskrun_store.py` | 有脚本化合成验收；真实模型完成率未测，Deep Agents 非默认在线业务 |
| M07 | Context 预算、来源保护、Skills、可选持久证据记忆 | `server/agent/context_policy.py` `server/agent/governed_evidence_memory.py` `server/agent/skills/` | 部分实现；动态工具渐进披露、Prompt Caching、负向记忆与长期对照未完整验收 |
| M13（主责） | 合成业务独立 SQL Oracle、模型运行对照、Bad Case、Holdout | `benchmarks/decision_card_business_eval.py` `benchmarks/model_run_comparison.py` | 8 个冻结合成任务；真实模型 Provider 执行、人工真值与统计稳健性未验证 |
| M14/M16（实验） | TaskRun Trace、评测计费归因、部署与前后端回归 | `server/observability/` `benchmarks/provider_cost_reconciliation.py` `docker-compose.yml` | 成本估价不是付款凭证；未形成生产灰度/真实 Provider 性能 |
| M11（沙箱实验） | Docker 无网络受限合成 Coding Agent 工作区 | `benchmarks/sandbox_workspace.py` | 合成任务 Docker 隔离不等于安全多租户 OS Sandbox |

**明确不做**：把高考顾问重构为 9 Agent 客服系统；重复创建企业级退款 Ledger 或完整多模态 RAG 检索服务。真实业务事实优先 SQL，政策类文字走检索；模型只在明确许可的局部范围自主决策。

## 第一批补全任务

| 编号 | 执行项 | 通过标准 | 当前状态 / 关联变更 |
| --- | --- | --- | --- |
| GB-P0-01 | 真实模型受约束多步业务 Eval | 固定 8 合成任务 + 扩大独立 Holdout；模型版实跑、独立 SQL Oracle、正确拒答、轨迹、provider token 与失败分类 | **PARTIAL**；可选模型执行接口已实现，仍未完成真实模型结果 |
| GB-P0-02 | 失败归因与任务覆盖审计 | 缺失案例/非法 identity/重复样本/Token 未知/意外拒答均被报告；不得把 scripted CI 当真实 LLM | **PR #143 Open**，未合并 |
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
