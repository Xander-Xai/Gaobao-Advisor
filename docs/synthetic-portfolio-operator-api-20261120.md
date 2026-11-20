# P14｜合成多候选 SQL 证据核验：受控 HTTP 入口

> 延续 #117 的 Draft Stack。只读、合成演示、默认关闭。不是志愿方案提交接口、用户真实录取咨询或可接收任意文本的 Agent。

## 接口

`POST /api/v1/admin/agent-experiments/portfolio`

- 复用 Admin Operator/Auth RBAC 和 `ENABLE_SYNTHETIC_AGENT_EXPERIMENT=true`；production 环境强制返回 404。
- 请求只能传 `province`、`score`、`subject_type`、`year`、`candidates`；每项仅 `school` 和 `major`，其他任意属性拒绝。必须 3–8 个不重复的已固定虚构院校/专业组合。
- 复用 P11 的 `PortfolioRequest`、真实离线合成 SQLite 只读查询与 `verify_synthetic_portfolio` 二次 SQL 核验。
- 最终响应严格区分 `accepted_historical_comparisons` 与 `excluded_candidates`，只含合成历史事实与来源；`admission_probability=null`、`application_ranking=null`、`human_review_complete=false`。
- 15 秒 HTTP 响应截止时间不是后台 Python Thread 的硬中断。只读工具无自带外部模型、网络或数据库写操作。
- 不接入公共 `/chat`，也不通过网页授予 Deep Agents 付费模型执行权限。

## 真实 HTTP 集成测试

隔离子进程创建合成 SQLite 与 Admin operator/viewer 身份，通过实际 FastAPI TestClient 验证：

1. 登录后的 operator 可执行 5 个合成候选，实际 SQL 核验 4 条完整历史比较、1 条缺证据排除；
2. 未登录 401、viewer 403；
3. 真实大学、输入额外指令字段、少于 3 个候选全部 422；
4. 实验关闭或 production 模式下 404。

```bash
pytest -q tests/test_synthetic_portfolio_http_e2e.py
```

## 仍缺少

- Vue 专门交互页面、TaskRun 审批/持久化的多候选执行版本；
- 脱敏且获得授权的真实考生/招生源数据、政策核对及专业组/考生位次/实际志愿顺序；
- 真实 LLM 自主执行成功率、成本与 Ground Truth 评测；
- 生产级沙箱、进程强制终止、跨主机任务恢复。
