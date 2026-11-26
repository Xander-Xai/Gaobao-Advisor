# P9｜真实模型合成业务评测的实验来源与配对对照

> 基于 Draft PR #112 之上的 P9 增量。该文档与代码仅为安全的可复现评测基础。当前未运行外部真实模型，**不等于**已有真实模型完成率、人工审核的 Ground Truth、招生预测准确率或成本节省结果。

## 目标与实现

过去已有 `benchmarks/decision_card_business_eval.py` 的 8 案例真实 SQL 独立评分和允许显式执行的 OpenAI-compatible Planner，但缺少一次实验的运行 ID、冻结 Prompt/工具契约指纹与**直接消费原始业务报告**的两轮 A/B 配对比较。

本切片增加：

- 每次评测报告包含随机 `synthetic_eval_` run ID、`SYNTHETIC_DECISION_CARD_V1` 合同、模型 ID、固定 temperature、system prompt SHA256、工具契约 SHA256、数据集 SHA256、Provider token 来源、总 Token 和是否满足对照条件；不写 API Key、原始模型对话和真实学生数据。
- `benchmarks/model_run_comparison.py` 仅消费两份已有原始报告。要求 **同模型 ID、同数据集 digest、同工具合同、同温度、8 条相同案例、4 条相同 Holdout**，检查分案例 Token 与总 Token 是否能对上。
- 明确区分 development 和 holdout 的正确完成数、正确拒答数、工具调用、provider tokens、简单耗时差异和回退案例；无价格表或第三方对账时美元成本始终为 null。
- 不论候选配置如何，即使 Holdout 非回退且 Token 下降，也仅输出 `REVIEW_REQUIRED`，并保持 `production_deployment_authorized=false`、`human_review_complete=false`、`statistical_significance=NOT_ESTABLISHED`。
- 两份报告中的“REAL_MODEL_NOT_SCRIPTED”及 Provider usage 字段只是**自报声明**；比较器无法从 JSON 证明外部模型真的被调用，必须另由受信操作员核对 API/账单/运行记录。本工具不对真实模型的定价作估算，也不触发模型 API。

## PR 与验收状态

本增量对应 [Draft PR #113](https://github.com/Xander-Xai/Gaobao-Advisor/pull/113)。代码提交不等于自动化验收；必须以同一提交 SHA 的 Actions 和 CodeQL 运行结果为准，不能复用 #112 的成功状态。

## 无 Key 的验证路径

```bash
python -m benchmarks.decision_card_business_eval --out /tmp/baseline-synthetic.json
pytest -q tests/test_model_run_comparison.py tests/test_decision_card_business_eval.py
```

确定性基准会正常运行，并生成 `comparison_eligible=false`；**不得**直接拿这个 JSON 与伪造的模型报告对比并声称 A/B 已完成。

## 真正的模型对照（需操作员明确授权与独立预算）

须先在隔离环境中配置自有 `OPENAI_API_KEY`，并选择其实际兼容的模型与可重复的提供方版本；不在 CI、网页或者 Git 里保存密钥。分别固定参数、提示词与数据，明确一项计划中的实验变量，执行两组完整 8 案例：

```bash
python -m benchmarks.decision_card_business_eval --mode model --model YOUR_MODEL_ID \
  --permit-external-model --max-cases 8 --out /tmp/model-baseline.json
# 应用经人工批准的唯一候选修改后，再跑同模型 ID、同数据集、同工具约束
python -m benchmarks.decision_card_business_eval --mode model --model YOUR_MODEL_ID \
  --permit-external-model --max-cases 8 --out /tmp/model-candidate.json

python -m benchmarks.model_run_comparison --baseline /tmp/model-baseline.json \
  --candidate /tmp/model-candidate.json --out /tmp/model-diff.json
```

**重要**：若两次模型/数据、工具合同或案例不同，配对会拒绝；Prompt digest 可以不同（这是允许的实验变量），但并不自动证明是唯一改变的因素。真正的可重复 A/B 至少还需要多个独立随机种子/批次、模型服务端版本记录、稳定性检验、人工审定 Ground Truth 和计费记录；4 个 Holdout 案例不能用于统计显著性结论。

## 尚无结果支持的评测结论

- 尚无真实模型主动工具调用的实跑 Token/美元费用或质量提升；
- 尚无合法授权的真实招生业务数据、真实学生反馈、真实招生效果；
- 没有模型自改 Prompt/Skills 并自动发布的生产闭环；没有持久化 Deep Agents 单次生成内部恢复；
- 本轮与历史 P0 路线仍需按 #103→#105→#106→#107→#108→#109→#110→#111→#112→P9 顺序审核合并。

## Repeated real-model A/B budget contract (2026-11-24)

Historical Gaobao Harness/Eval change recorded as of 2026-11-24. No provider calls or measured outcomes are claimed.

The repeated model experiment now exposes per-arm request caps and provider-reported token stop thresholds in the no-network plan, and forwards exactly those settings to every child. New executed runs require matching reconciled provider budget receipts before launching another arm. Offline historical comparisons remain readable but cannot mix legacy and new budget receipts across trials.

```bash
python -m benchmarks.repeated_model_experiment --model YOUR_VERSIONED_MODEL \
  --out-dir /tmp/gaobao-eval-new --trials 2 \
  --max-provider-calls-per-arm 64 --stop-after-reported-tokens-per-arm 120000
```

The example is a dry run. Add --execute --permit-external-model --confirm-provider-charges only with an approved key, reviewed provider and budget. Token stop thresholds are checked after responses, not hard token or monetary caps; the final request can overshoot. The sum is a planning value, not a cost guarantee. Complete paired synthetic reports still need independent billing and human review. No real admissions data or production automatic rollout.

```bash
pytest -q tests/test_repeated_model_experiment.py tests/test_model_run_comparison.py
```

After each completed paid arm, the runner also checks the frozen dataset/tool/temperature contract, case partitions, unique run identity and stable, distinct prompt digests against all preceding arms. A mismatch stops further provider spending before the next trial; no incomplete summary is published. Negative controls use stub responses only.
