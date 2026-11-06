# Gaobao Advisor Java/Python 可复现负载基准

## 设计目标

这是一组**测量工具**，不是事先宣称已经达到 50/100/200 并发容量。
只有 Locust 实际完成的请求算作吞吐样本；包含所有失败请求并计入错误率。
Java 目录、Python 数据接口与无 Key AI SSE 分开报告。未配置真实模型密钥时，
任何对话指标都**不是第三方 LLM 生成速度、首 Token 延迟或真实志愿推理准确率**。

## 测试场景与基准

| 场景 | 端点 | CI 用户数 | 数据 |
|---|---|---|---|
| single-gateway / catalog | GET /api/v1/catalog/schools?limit=10 | 50 | SYNTHETIC_DEMO |
| dual-gateway / catalog | 同上 | 50、100、200 | SYNTHETIC_DEMO |
| dual-gateway / python | GET /api/v1/data/schools?limit=10 | 50、100、200 | SYNTHETIC_DEMO |
| dual-gateway / python-overview | GET /api/v1/data/overview | 50 | SYNTHETIC_DEMO |
| dual-gateway / chat | POST /api/v1/chat，读取完整 SSE done | 5、10 | 无 LLM Key |

全部通过 Nginx 入口，固定在同一个 GitHub-hosted Ubuntu + Docker Compose 机器上执行；
负载生成器也运行于这台主机，因此单机的 CPU/网络竞争可能影响结果。
Locust 使用每用户约 1 秒一次请求（chat 2 秒），记录请求失败而不隐去 429/5xx。
单副本与双副本对照是**拓扑差异试验**，不是对“性能提升”的预设结论。

## 指标与可复现

入口：`benchmarks/load/locustfile.py`；
驱动：`benchmarks/load/run_suite.py`；CI：`.github/workflows/java-services.yml`。
在已启动 Java overlay 且拥有 `locust==2.43.4` 的机器上：

~~~bash
python benchmarks/load/run_suite.py --scenario catalog --tiers 50,100,200 --duration 10 --label dual-gateway
python benchmarks/load/run_suite.py --scenario python --tiers 50,100,200 --duration 10 --label dual-gateway
python benchmarks/load/run_suite.py --scenario python-overview --tiers 50 --duration 10 --label dual-gateway --informational
python benchmarks/load/run_suite.py --scenario chat --tiers 5,10 --duration 10 --label dual-gateway
~~~

输出到 gitignored 的 `benchmarks/load/results/`，包含各档的
`*_stats.csv`、`*_stats_history.csv`、`*_failures.csv` 和
`.json`（总请求、失败、错误率、RPS、p50/p95/p99、Docker CPU/内存样本）。
CI 会将这些文件作为 `java-python-load-evidence` artifact 附在实际 Actions run 上。
**CI 验收口径（Issue #90）：** `python-overview` 是**诊断性负载观测**，不是功能放行门槛；只有该场景允许显式 `--informational`。功能正确性仍由真实 Compose 环境中的 `/api/v1/data/overview` HTTP 200 和合成数据标记校验，及其后的 SSE、目录契约、跨实例限流和 Redis/MySQL 故障注入负责验证；其他负载场景仍须产生已完成请求。该例外**不代表概览接口性能通过**。其他功能性负载场景若 0 个已完成请求或已完成请求 **100% 失败**，仍会阻断 CI，且保留原始证据；这不是 p95/SLA 阈值。

无 Key 聊天负载必须发送满足 `ChatRequest` 契约的 `session_id`。匿名请求中的该值不具备鉴权能力，服务端会重新生成 session ID；压测仅断言 HTTP 200、SSE Content-Type 和 `done` 事件，不把这一流程称为已认证会话复用测试。

当 Locust 返回 0 个已完成请求时，保留 CSV 和 JSON，记录 `result_status=NO_COMPLETED_REQUESTS`、`request_count=0`、`error_rate_pct=null`、`p50_ms/p95_ms/p99_ms=null`，避免把没有样本错误地标成 0% 或 100% 错误率。所有已完成请求均失败时标记 `ALL_FAILED`、错误率 100%。异常数据即使不使功能 CI 失败，也必须在性能评审中明确展示、继续优化。当前短时采样为 8 秒，而单请求超时为 10 秒；因此零完成样本不能单独证明后端的最大吞吐或真实用户 SLA。

`peak_container_cpu_pct` 是采样到的**单个容器峰值**，不是机器的 CPU 总利用率；
`peak_container_memory_mib` 同理。Docker stats 为低频观测，不能代替火焰图。

测试脚本只在 CI 范围内将公开 API 与目录的 Token 桶额度提高到 3000/s；
常规 Java overlay 的默认 20/s、突发 40 和原 Python IP 限流保持不变。
**不要照搬测试限额部署公网。**

## 如何解释和验收

1. 逐档检查请求数、RPS、P95/P99、失败率及有无 429/5xx。
2. 比较 50 用户单网关/双网关的结果，观察性能是否增长、下降或无显著变化；
   不同阶段会存在 Redis/MySQL 缓存和 JIT 热身差异，不能据此证明线性扩容。
3. 对 Python 查询观察 CPU/内存；对于长事务/LLM，另做接入真实模型的首 Token
   与端到端延迟实验，并记录 provider 与成本。
4. 生产放行仍需要独立压测机、真实性数据集、实际用户并发模型、CPU/内存限额、
   长时稳定性、TLS、数据库专用权限以及跨主机故障演练。

不允许将合成数据的性能或容错验证包装成真实考生的推荐效果或 SLA。

## 第一次压力测试识别出的瓶颈（未掩盖失败）

GitHub Actions run [42176296935](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42176296935) 使用 500 所额外虚构院校、12,000 条录取数据和 6,000 条计划数据，在 8 秒短时采样中得到 Java Catalog 双网关 200 用户 210.24 RPS / P95 720 ms / 0% 错误；Python **重聚合** `/api/v1/data/overview` 在 100 用户时退化为 9.27 RPS、P95 10,000 ms、错误率 85.29%，200 用户档位未采集到有效完成请求，严格验收任务因此 FAIL。

为区别接口复杂度，后续正式压测将 Python **分页列表** `/api/v1/data/schools?limit=10` 对照 Java 目录查询（50/100/200 用户），并把昂贵的概览聚合接口单独以 50 用户保留为负载剖析样本，绝不将分页成绩代替聚合成绩。上面的故障证据仍作为工程瓶颈保留。后续建议针对概览做 SQL profile、缓存策略/索引或异步预聚合，再进行有验证的前后对比。
