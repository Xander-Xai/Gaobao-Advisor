# P25 — 已实现与待验收边界（2026-11-20）

该切片建立在 Draft #128 上，保持原有 stacked Draft 合并顺序。**不改变 master、公共 /chat、真实招生数据或生产部署。**

## 代码变化

1. Context：只读 Agent 历史观察不再任意截断 JSON；仅保留来源 ID 的降级观察必须显式标记 observation_truncated。极小 Context 预算导致 context_budget_exhausted，并正确计入已经调用的工具与 Trace。
2. Sandbox：合成 Docker workspace 拒绝目录符号链接、内部 symlink、嵌套目录、异常 marker、过多文件、超大文件。仍不是强化的多租户生产隔离。
3. Token / Cost：新增 provider_cost_reconciliation 的纯离线口径：同模型、同版本化测试合同、同 8 条合成任务的 usage 汇总，按显式日期和 HTTPS 来源的价格快照计算预计美元成本与每成功案例估价。可以对照输入的声明账单，但绝不因为本地 JSON 相等就声称真实 Provider 收据已被认证。
4. 有对应的反例单元测试：预算不足、无效 JSON、来源丢失、Trace 数量不一致、沙箱链接与目录逃逸、负价/NaN、伪装模型及账单用量不一致。

## 本轮没有伪造的完成项

仍无真实模型调用证明、独立专家 Ground Truth、真实招生信息许可、真实用户交付指标、支付账单校验、生产级分布式 OTel、长期个人隐私全量删除、多租户沙箱证明或生产灰度回滚。因此这些项目保持 NOT_MEASURED / NO-GO，不允许用合成 CI 伪造收益或简历生产经历。

## 验收指令

pytest -q tests/test_context_governance.py tests/test_sandbox_workspace_governance.py tests/test_provider_cost_reconciliation.py tests/test_readonly_harness.py tests/test_decision_card_trace_eval.py tests/test_sandbox_workspace.py --no-cov -o addopts=''

之后运行完整 CI（Python3.10/3.11、Frontend、MySQL/Redis、Compose、Security、CodeQL），并记录精确提交 SHA 的结果。

## Cost 估算输入示例（均为人工提供的快照，不联网）

rate-card.json:

```json
{
  "model_id": "test-provider-model",
  "currency": "USD",
  "as_of_date": "2026-11-20",
  "source_url": "https://example.invalid/pricing",
  "input_usd_per_million_tokens": "2.50",
  "output_usd_per_million_tokens": "10.00"
}
```

命令：python -m benchmarks.provider_cost_reconciliation --report synthetic-eval.json --rate-card rate-card.json --out cost-estimate.json

以上金额是假设的**测试价格**，不是任意真实模型的当前定价。输出中的 provider_billing_reconciled 永远为 false；只有可信供应商结算记录、价格版本与真实运行身份都核验后，才能另行建立实际账单对账。
\n## Memory 增量（与生产长期记忆不同）\n\n已消除 MemoryManager / Graph 持久化失败时将原始异常文本写入日志或 Trace 的风险；增加有界的合成证据记忆过期指针清理方法 prune_expired（仅清理索引、不删除 TaskRun / 审计）。仍不构成真实考生资料的个人信息完全删除或自动跨会话记忆。\n