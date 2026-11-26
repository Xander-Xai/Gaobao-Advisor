# P24｜Agent 逐步执行 Trace：真实 Tool Loop 事件、模型用量、失败根因与版本回归

**范围：** `SYNTHETIC_DEMO`；本切片基于 Draft #127，不接入用户真实志愿、外部政策或生产发布。运行时 `server/agent/readonly_harness.py` 是已有的只读受控 Loop，不是通用 coding sandbox。不要把它误称为模型内部思考过程、全量 OpenTelemetry 链路或生产可观测平台。

## 此次补齐的真实工程缺口

- 以前 Harness 返回 `tool_sequence` 和粗略 `trace`，但某些终止原因（例如模型请求异常、预算触发）没有独立的失败事件，且无法对账每一次模型决策的 Token 与耗时；单个业务评测结果只保留总步数、总工具次数。
- 现在每次**模型决策尝试**都写**固定字段、明确终止原因**的事件：step、kind、result、固定工具名、合法证据计数、工具/模型调用耗时、Provider 自报本步 Prompt/Completion Token。没有原始模型回复、考生画像、工具参数、学校名称、数据库值、source_ids 或异常消息。
- 对于 Provider 请求抛异常或非法 planner 结果，`usage_known=False`：即使外部 Provider 已计费也无法从异常获知，**不得以 0 Token 伪装真实成本**。
- `benchmarks/decision_card_trace_eval.py` 的独立结构验证器要求逐步事件序号、工具 allowlist、实际执行次数、终态、每步 Token 汇总与任务报告严格对应，禁止不明字段进入输出。生成 SHA256 **完整性指纹**，不宣称 Provider 数字签名或独立来源鉴证。
- `benchmarks/decision_card_business_eval.py` 把每条合成 SQL 业务任务的 Trace Receipt 纳入报告；原独立 SQL Oracle 仍验证业务事实，只有两种检查都通过才算 Task Pass。若任何轨迹不完整，真实模型对照报告不得进入可比较状态。
- `benchmarks/model_run_comparison.py` 对声明 `SYNTHETIC_AGENT_STEP_TRACE_V1` 的新模型报告逐条重算轨迹校验；老报告未声明 Trace 时可继续按旧口径比较，**不可宣称补齐逐步 Trace**。
- `benchmarks/synthetic_optimization_review.py` 仅根据 development 分区的**经过核验的失败终态**归因；Holdout 仍只作为评测门禁，不将其失败原因用于自动提出优化假设。

## 能实际证明的指标

| 维度 | 可观测范围 |
|---|---|
| 模型决策步数 | 每次 planner 调用的一条结构化终态记录 |
| Tool Calls | 实际调用 Handler 的次数（不是一个图 Node 就算一次工具调用） |
| 模型 Token | Provider 成功响应自报的 step usage；错误/缺失归为未知 |
| 模型与工具耗时 | 本地 monotonic wall-clock 计时；不等于独立 Provider 账单 |
| 失败原因 | 有界枚举：planner_failed、tool_schema_invalid、tool_repeat_loop、tool_execution_failed、provider_usage_missing 等 |
| 策略回归 | SQL 独立 Oracle + 每条轨迹合同 + 相同模型/数据/工具/A/B 切分 |
| 完整模型内部推理 / 多服务 OTel | **未完成，不宣称支持** |
| 人工专家 Ground Truth / 真实考生交付率 | **未测量** |

## 无密钥重现

```bash
python -m benchmarks.decision_card_business_eval --out /tmp/gaobao-trace-baseline.json
python -m pytest tests/test_decision_card_trace_eval.py -q --no-cov -o addopts=''
python -m pytest tests/test_decision_card_business_eval.py tests/test_repeated_model_experiment.py -q --no-cov -o addopts=''
ruff check .
ruff format --check .
```

输出仍是 deterministic scripted controller，不调用模型，Prompt/Completion Token 均为 `null`，不是 `0`。只有显式外部 API 授权的 PR #125 模型评测模式才能获取 provider 自报 usage。CI fake client 的 usage 是测试夹具，不能作为真实模型性能证据。

## 剩余未实现条件

1. **生产跨服务 Trace**：尚没有在 SQL/Redis/Worker/SSE/Provider 等所有服务贯通 OTel span 和采样/告警/高基数治理。
2. **Provider 实际调用与对账**：当前没有独立 Provider 收据/账单，也没有真实 A/B 优化数字。
3. **高考数据可靠性**：缺权威真实招生数据来源、版本、许可及专业审核；只适用于虚构示例。
4. **真实灰度与回滚**：PR #127 中的指针不影响生产。此版本只提升可诊断性，不擅自接通生产发布。
5. **LLM 内部完整轨迹**：不会也不能从 API 获得模型私有内部推理过程；本切片是可观察的公开决策行动及结果的逐步 Trace。
6. **Loop 通用性**：固定的只读招录查询不等于可以自主改代码、执行命令、运行容器的 Coding Agent。

所有代码仍保持 Draft 的分支链和功能开关，先审查再合并。
