# P30 · Agent Trace / Observability 工程增量（2026-11-20）

> 基于 Draft PR #133 的独立 Draft 增量；仅合成高考任务。原 public /chat 与生产发布状态不变。未验证生产采集端、真实学生数据或商业 SLA。

## 变更

- **HTTP → TaskRun → Worker**：沿用 P26 持久化的私有 W3C traceparent。FastAPI 与独立 Worker 现在分别初始化可选 OTLP exporter，Worker 退出前 flush。Trace ID 不放入公开 TaskRun JSON。
- **阶段 Span**：HTTP request / TaskRun / Deep Agents runtime / planner / SQL tool / receipt verification 均使用固定 Span 名；无 prompt、学生画像、SQL 行、密钥、异常原文作为属性。HTTP status 和业务最终状态只记录有限枚举。
- **低基数指标**：HTTP Prometheus 标签优先使用路由模板；中间件拿不到模板时退回静态业务分组，不再把 `/runs/<ID>` 写进 label。新增 `agent_taskrun_attempts_total{engine,status,reason}`，仅统计执行尝试次数，不是唯一任务数，也不等于考生有效交付数。
- **跨进程指标边界**：短命 Worker 中的 `agent_taskrun_attempts_total` 只是本进程尝试计数，进程退出后 FastAPI 不能直接抓取它。新增 `agent_taskrun_stored_jobs{engine,status}`：仅在显式开启合成实验并配置独立 SQLite Journal 时，由 API `/metrics` 只读汇总持久化任务当前状态。该 Gauge 不是请求/尝试历史，也不能替代完整的生产 Metrics Pipeline。
- **故障归因**：任务执行状态区分 SUCCEEDED / NEEDS_REVIEW / FAILED / CANCELLED / ERROR；失败原因仅允许固定归类。现有 agent_safe_spans_total 和 duration_seconds 可以定位阶段异常与时延。

## 本地验证（不需要付费模型或外部连接）

```bash
pytest -q tests/test_agent_observability_p30.py tests/test_agent_trace_propagation.py --override-ini addopts=''
python -m compileall -q server/observability server/agent/taskrun_worker.py server/metrics.py
```

验收应通过同一 HEAD 的 CI Python 3.10/3.11、Full Compose、MySQL/Redis、可选 Deep Agents SDK（无外部模型）、Frontend、安全和 CodeQL。

## 可选真实链路采集（只限受控合成实验）

1. 自行部署受保护的 OTLP collector（仅 `https`，或本机 loopback `http`）。安装选配依赖 `opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`，并确保 FastAPI 与单独 Worker 都有相同配置。
2. 设置 `AGENT_OTEL_OTLP_ENDPOINT=https://<trusted-collector>/v1/traces` 和 `AGENT_OTEL_SAMPLE_RATE=1` 仅用于本地合成任务验证；其他 Agent 实验开关按已有文档默认关闭或显式配置。
3. 用 operator 创建任务、不同 admin 审批、Worker 执行同一合成 TaskRun。在 collector 中按 trace ID 核对 HTTP root、Worker continuation、runtime/tool/verify Span 的父子关系；观察 `/metrics` 是否只有静态 `endpoint`、`engine`、`status`、`reason` 标签。
4. 注入不合法工具参数、来源缺失、取消/租约冲突；验证状态和计数，不采集秘密、不因 OTel 故障放宽业务审核。比较数量与实际批准的 TaskRun 尝试次数。

**尚未验证**：真实 collector 回放、跨主机采样协调、日志与 Trace 统一采集、生产 SLO、基于真实模型的 Token 成本、跨租户数据隔离与任何真实招生效果。没有采集结果时，不写“已完成生产级全链路追踪”。
