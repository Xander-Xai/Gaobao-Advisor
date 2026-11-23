# 2026 业务 Agent / AI 全栈项目定位与验收

## 经历定位
本仓归入**最近的另一段任职经历**，负责证明业务建模、LangGraph 工作流、FastAPI/Vue、SQL + RAG、SSE、可执行 Agent Loop 与业务验收。此前同一段任职的两项技术专项分别是 Customer-Service-AI-Agent（Runtime）和 Beauty-Industry-RAG-QA-System（知识检索）。

公开仓库存在上游 MIT 代码的二次开发历史，简历中必须区分自己的贡献与上游原有能力。合成数据不得宣称为真实学校/考生评估。

## 面试可复现入口
- 在线业务主线：`server/graph/graph.py`、`server/routes/`、`frontend/`；17 节点单图，不得称为客服式多 Agent。
- 独立 Harness：`server/agent/readonly_harness.py`；它不是在线默认业务执行路由。
- 业务 Eval：`benchmarks/decision_card_business_eval.py` 使用独立 SQL 真值评分，冻结 8 个合成案例（4 开发/4 holdout），真实模型调用默认关闭。

## 本轮代码增量：模型业务失败归因
`benchmarks/model_eval_triage.py` 对已生成的模型模式报告做契约验证和失败分桶：分别列开发/holdout 的失败 case、错误码、有效拒答、已报告 Token；不把自称真实模型的报告当成外部真实认证。

```bash
python -m benchmarks.decision_card_business_eval --out /tmp/gaobao-baseline.json
# 仅在自行配置 API Key、明确授权外部调用及接受费用后：
python -m benchmarks.decision_card_business_eval --mode model --model YOUR_MODEL_ID --permit-external-model --max-cases 8 --out /tmp/gaobao-model.json
python -m benchmarks.model_eval_triage --report /tmp/gaobao-model.json --out /tmp/gaobao-triage.json
pytest -q tests/test_model_eval_triage.py
```

**本轮没有执行真实模型调用。** 当前开放的多条 Harness/质量 PR 仍应按各自 CI 验收。真实招生数据授权、专家业务复核、同模型多次 A/B、真实模型成本/成功率和公网 SLA 仍待验证。具体项目演示应走前端→API→图→SQL/RAG→来源/风险校验→SSE 的端到端路径。
