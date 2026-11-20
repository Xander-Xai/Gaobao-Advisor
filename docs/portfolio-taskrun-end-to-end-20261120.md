# P15｜多候选合成历史证据从离线接口接入 TaskRun 审批与持久化执行

> 本阶段仅在 `SYNTHETIC_DEMO` 环境复现，禁止真实招生建议及个人考生数据。无需付费模型，结果只是**历史证据核验任务**，不是审核通过的完整志愿方案。

## 实际实现

- `POST /api/v1/admin/agent-taskruns/portfolio-runs`：仅 operator/admin（原有鉴权），接受 `portfolio` + `idempotency_key`，严格校验 3–8 个虚构院校—专业组合、学校名称、专业名称、年份、选科、分数和禁止任意额外字段（含恶意传入的 model/engine/prompt）。创建后状态 `PENDING_APPROVAL`，Engine 固定为 `portfolio`。
- `POST /api/v1/admin/agent-taskruns/runs/{id}/approve`：原有 maker-checker，必须由不同的管理员审批。
- `POST /api/v1/admin/agent-taskruns/runs/{id}/execute`：允许 **deterministic / portfolio**，永不从 HTTP 执行收费 `deepagents`；Worker 通过 journal lease / CAS，检查取消，运行真实隔离合成 SQL，第二次 SQL 再核验后写入持久化结果。
- `GET .../runs/{id}` 与 `POST .../runs/{id}/cancel`：复用 owner/admin 访问控制、取消和旧 Worker fencing。
- 部分候选有完整**历史 SQL**依据：TaskRun `SUCCEEDED`（只是历史证据筛选完成）；所有候选均不具备合格依据：TaskRun `NEEDS_REVIEW`，显式保持无可用候选。即使 `SUCCEEDED`，输出中的 `human_review_complete=false`、`application_ranking=null`、`admission_probability=null` 必须保持不变。

## 可复现的 HTTP E2E

```bash
pytest -q tests/test_portfolio_taskrun_http_e2e.py
```

独立子进程中初始化 100% 合成招生数据库与独立任务 journal，通过真实 FastAPI TestClient 和虚构 operator/admin/viewer 完成：创建/幂等冲突/权限校验/双人审批/5 候选 SQL 执行（4 条历史记录可核验，1 条排除）/0 候选安全拒答/取消/自审批拒绝/生产和开关关闭时拒绝。

## 剩余

1. 当前是本地单节点 SQLite TaskRun 的历史事实核验，不是有实际考生或招生政策来源的志愿方案交付，不具备生产分布式容量及隔离保障。
2. 当前 `verify_synthetic_portfolio` 再次调用同一决策计算实现；它可以发现运行期间数据变化与结果篡改，但**不是独立的业务判定 Oracle**。后续必须建立基于数据库直接查询的独立判定器及冻结 Ground Truth。
3. 未调用真实 OpenAI/其他 Provider，因此没有真实模型成功率、Token、账单或成本优化证据。
4. `Deep Agents` 内部状态尚未跨进程 checkpoint；取消为协作式，不能停止已发出的 Provider 请求。
5. 所有上游 PR #103–#118 仍为 Draft，当前内容未合并主分支。
