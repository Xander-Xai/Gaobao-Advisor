# P21｜高考顾问 Agent：可重复的真实模型 Harness A/B 实验

> 范围仅限 `SYNTHETIC_DEMO` 事实工具；不涉及实际招生数据、真实考生、模型自行部署。所有评测结果先视为实验声明，不是对生产准确率、费用节省或统计显著性的证明。

## 此次交付

- `server/agent/decision_card_model_planner.py`：两种**冻结 Prompt**，`baseline` 和 `evidence_first`。第二种强调先证据、逐个执行剩余类型化工具，缺失时早拒答。两者使用**相同模型、相同 SQL 工具、相同安全门禁、相同证据评分器**；不提供任意自由 Prompt 或 Shell 权限。
- `benchmarks/decision_card_business_eval.py`：增加 `--prompt-variant`；在单次报告中记录候选 Prompt 名称和实际 Prompt SHA256。默认完全保留无外部调用的确定性业务基线。
- `benchmarks/repeated_model_experiment.py`：2–5 对 A/B 全套 8 案例实验，交替运行先后顺序；检查每一组相同模型、温度、SQL 数据集、工具契约、Development/Holdout 分割；检查每个实验臂 Prompt 哈希在所有试次固定、运行 ID 不可复用，独立收集 **Holdout 的正确完成、失败回退、Token、工具调用与中位 Token 差额**。
- 只有全部试次 Holdout **不退化**且每次 Token 都下降，候选才标记 `REVIEW_REQUIRED`；出现任何已通过案例回退即 `REJECT_CANDIDATE`。无论结果如何：`production_deployment_authorized=false`。

## 默认：只预览，不付费、不联网

```bash
python -m benchmarks.repeated_model_experiment --model YOUR_FIXED_MODEL_ID \
  --trials 2 --out-dir /tmp/gaobao-ab
```

默认输出计划，**不会生成文件**、不会使用 `OPENAI_API_KEY`、不会访问 Provider。预览列出最多模型决策步数，但并非美元费用上限（Provider 模型定价与实际 Prompt 大小可能不同）。

测试：

```bash
pytest -q tests/test_repeated_model_experiment.py tests/test_decision_card_business_eval.py \
  tests/test_decision_card_model_planner.py tests/test_model_run_comparison.py
```

## 真实模型执行门禁

用户明确授权本实验，并自行配置有效 Provider API Key / 费用预算后，才可在隔离环境运行：

```bash
export OPENAI_API_KEY=YOUR_PRIVATE_KEY
python -m benchmarks.repeated_model_experiment \
  --model YOUR_FIXED_MODEL_ID --trials 2 --out-dir /tmp/gaobao-ab \
  --execute --permit-external-model --confirm-provider-charges
```

**注意：** 以上是操作指引，不意味着本次执行了这些命令。不要把真实 Key 提交到 Git、CI、日志或聊天。即使有调用次数和时间上限，项目没有权威 Provider 定价信息，因此**不能保证美元最大支出**。结果来自 Provider 自报 token usage，未完成账单对账。

报告路径：`trial-N-baseline.json`、`trial-N-evidence_first.json` 及 `repeated-experiment-summary.json`。没有模型密钥时不得使用 Fake Runner 生成“真实模型”报告；CI 只测合同、模拟负例和默认安全行为。

## 尚未完成的产品评测条件

1. **没有实际真实模型 A/B 数据**：本轮不调用 Provider，不宣称提升比例、真实任务完成率或成本降幅。
2. **Ground Truth 尚未真人专家审核**：8 个合成案例仍为开发者编写，尽管 Oracle 独立查 SQL，也不代表真实志愿建议正确。
3. **服务端版本漂移无法彻底阻止**：模型 ID 相同不等于服务端权重和推理系统绝对不变；要记录 Provider 版本/请求 ID、对照实际账单，必要时重跑。
4. **Holdout 太小**：4 条案例重复 2–5 次不是 8–20 个独立招生案例；`statistical_significance=NOT_ESTABLISHED`。
5. **不能替代真实 Agent 多轮恢复与生产 SLA 测试**：此前跨进程恢复只证明受控合成工具/注入图路径。

## 合并及下一步

本切片基于 Draft #124，仍应作为 Draft PR 审核，**不直接合并 master**。待 CI + CodeQL 在本切片 SHA 成功，再评估是否提交真实模型测试预算和单独人工审核任务集。保留已有 #103–#124 原始依赖，不重复造底座。
