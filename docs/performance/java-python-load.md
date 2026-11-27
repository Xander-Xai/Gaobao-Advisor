> **状态（2026-11-07，合并后更新）：** Java 微服务（PR [#89](https://github.com/Xander-Xai/gaobao-advisor/pull/89)，Squash `7c87686`）已合入 `master`。`master` 默认部署仍为 Python/FastAPI + SQLite，Java 为可选 overlay。公网生产仍为 **NO-GO**。

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
python benchmarks/load/run_suite.py --scenario catalog --tiers 50,100,200 --duration 10 --label dual-gateway --informational
python benchmarks/load/run_suite.py --scenario python --tiers 50,100,200 --duration 10 --label dual-gateway --informational
python benchmarks/load/run_suite.py --scenario python-overview --tiers 50 --duration 10 --label dual-gateway --informational
python benchmarks/load/run_suite.py --scenario chat --tiers 5,10 --duration 10 --label dual-gateway --informational
~~~

输出到 gitignored 的 `benchmarks/load/results/`，包含各档的
`*_stats.csv`、`*_stats_history.csv`、`*_failures.csv` 和
`.json`（总请求、失败、错误率、RPS、p50/p95/p99、Docker CPU/内存样本）。
CI 会将这些文件作为 `java-python-load-evidence` artifact 附在实际 Actions run 上。
**CI 验收口径（Issue #90）：** 所有 Locust 负载档位都属于**诊断性观测**，CI 调用时明确使用 `--informational`，不把 8 秒采样下的“零完成”误判成功能回归。功能硬门槛仍由真实 Compose 中的 HTTP 200 / 合成数据标记、SSE（含 `done` 与会话鉴权）、Java/Python 目录契约、最小权限、跨网关限流、Nginx HA、Redis/MySQL 故障注入承担。无 `--informational` 的手动运行仍对零完成或 100% 失败保持非零退出码。**观测 CI 绿灯不等于负载容量、错误率、p95 或生产 SLA 达标**；负载缺陷必须从 artifact 独立评估（#91 已由 PR #93 关闭，其余负载观察项仍随每次 run 的 artifact 复评）。

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

## Issue #91 优化记录（perf/overview-aggregation，已合并 PR #93）

**根因（证据）：** `GET /api/v1/data/overview` 原实现每请求发出约 16 次顺序
SQL 查询（6 次 COUNT + 4 次 DISTINCT + 年份×2 + 2 次 DISTINCT COUNT + profile +
snapshot）。同步顺序压测显示进程内线程并发下每次往返被显著放大。

**优化：** 6 个 COUNT + 2 个 DISTINCT COUNT 合批为单个标量子查询语句；
`GROUP BY year` 一次返回 `years` 与 `scores_by_year`。往返次数 16 → 9，响应
字段/类型/排序完全不变。已用 `EXPLAIN QUERY PLAN` 确认三条聚合均走覆盖索引
（`ix_adm_lookup` / `sqlite_autoindex_enrollment_plans_1` / `ix_adm_year`），无新增
索引需求；未新增迁移，SQLite/MySQL 兼容。

**前后对照（同 fixture：500 院校/3,000 录取行/1,000 计划行；15s 窗口>10s 客户端
超时；同进程多线程，单机合成数据）：**

| 并发 | 优化前 median | 优化后 median | 优化前 p95 | 优化后 p95 |
|---|---|---|---|---|
| 1 | 4.93ms | 3.69ms | 5.65ms | 5.26ms |
| 5 | 80.25ms | 34.32ms | 123.22ms | 48.19ms |
| 10 | 189.46ms | 84.50ms | 266.35ms | 115.69ms |
| 20 | 266.58ms | 120.38ms | 395.26ms | 155.89ms |
| 50 | 239.64ms | 124.28ms | 332.27ms | 179.45ms |

50 并发下 median -48%、p95 -46%、吞吐 62 → ~101-109 req/s（+65%）。单机线程
压测含 GIL 争用，不同轮次有 ±10% 抖动；指标仅为合成数据相对对照，已有
`tests/test_data_catalog.py::TestOverview::test_overview_batched_counts_match_grouped_years`
守护批量计数与 `scores_by_year` 一致性。

**边界：** 这是单点聚合查询优化，不能消除 SQLite 单进程连接池/CI 共享 Runner
波动，不能宣称生产容量或 SLA 达标。Issue #91 已随 PR #93（`6a7af10`）合并而
关闭；真实网络/多进程与 MySQL 环境仍需独立复验。

### 复现脚本（仓库内，Issue #91）

`benchmarks/overview_perf_bench.py`：固定合成 fixture（种子 42；500 院校 /
3,000 录取行 / 1,000 计划行），SQLite 临时库；30s 窗口（>10s 客户端超时）×
3 轮重复；Python 版本随脚本输出。优化前后同脚本对照（波动范围为三轮 min–max）：

| 并发 | 优化前 median | 优化后 median | 优化前 RPS | 优化后 RPS |
|---|---|---|---|---|
| 10 | 146.59–156.01ms | 87.60–88.84ms | 57.86–59.92 req/s | 101.06–111.35 req/s |
| 20 | 224.87–231.89ms | 130.25–132.36ms | 60.42–61.80 req/s | 105.16–108.56 req/s |
| 50 | 224.35–231.71ms | 123.73–128.72ms | 61.66–64.52 req/s | 108.43–113.49 req/s |

零错误；单机线程压测含 GIL 争用。15s 历史观测（优化前 50 并发 median
239.64ms / p95 332.27ms）保留在上文。

## Distributed rate-limiter service measurements (2026-11-27 CI run)

The updated source was built and exercised by the `java-services` workflow associated with PR #221 (latest run [46057859459](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/46057859459); PR head `2aa5c9353c3c7ed725f090d83af5717d87216c5c`, Locust artifact merge-ref SHA `e5592627f072cc02ae646229491bd08e600bbdbc`). The workflow used a GitHub-hosted `ubuntu-latest` Docker host, synthetic MySQL dataset (scaled to 500 schools), Redis 7, Java Catalog, one then two Gateway replicas, Python API, and Nginx ingress. The load generator and services shared one host. Exact runner CPU/memory limits were not captured in the artifacts, so these results are short-run observations, not capacity/SLA claims.

During the Locust profiles, the workflow deliberately raised `CATALOG_RATE_PER_SECOND/BURST` and `PUBLIC_API_RATE_PER_SECOND/BURST` to `3000`; these runs exercise the Java Redis Lua path but are below the configured cap and produced no 429s. The Python service used the standard development profile's process-local bucket, not the Industrial Redis per-IP bucket. These Python route measurements describe API service load, not distributed Python limiter performance. After load profiles, the existing fault test restored Java Catalog to 1 token/s and burst 4; the dual Gateway check observed 4/20 allowed and 429s across both replicas. Full details/artifacts: [Actions run 46047930404](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/46047930404), artifact `java-python-load-evidence`.

| Route / topology | Users | Requests | RPS | P50 / P95 / P99 | Failures | Redis CPU / memory sampled during profile |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Java Catalog / single Gateway | 50 | 350 | 55.23 | 210 / 350 / 390 ms | 0 (0%) | peak 0.92% / 4.20 MiB |
| Java Catalog / dual Gateway | 50 | 350 | 55.71 | 150 / 1,000 / 1,100 ms | 0 (0%) | peak 1.04% / 4.21 MiB |
| Java Catalog / dual Gateway | 100 | 700 | 105.85 | 320 / 560 / 620 ms | 0 (0%) | peak 3.89% / 4.46 MiB |
| Java Catalog / dual Gateway | 200 | 1,492 | 185.70 | 670 / 1,400 / 1,900 ms | 0 (0%) | peak 2.66% / 4.22 MiB |
| Python schools / dual Gateway, development profile | 50 | 350 | 54.94 | 310 / 460 / 470 ms | 0 (0%) | peak 0.50% / 3.99 MiB |
| Python schools / dual Gateway, development profile | 100 | 113 | 10.26 | 10,000 / 10,000 / 10,000 ms | 66 (58.41%) | peak 3.17% / 4.22 MiB |
| Python schools / dual Gateway, development profile | 200 | 0 | N/A | N/A | No completed samples | Not a capacity sample |
| Python overview, dual Gateway | 50 | 0 | N/A | N/A | No completed samples | Not a capacity sample |
| No-key chat SSE, dual Gateway | 5 | 20 | 3.28 | 95 / 120 / 120 ms | 0 (0%) | peak 0.44% / 4.25 MiB |
| No-key chat SSE, dual Gateway | 10 | 40 | 6.43 | 210 / 320 / 320 ms | 0 (0%) | peak 0.48% / 4.46 MiB |

The separate post-load shared-bucket check used two Java Gateway replicas with `CATALOG_RATE_PER_SECOND=1` and `CATALOG_RATE_BURST=4`: **4 admitted / 16 returned 429** from 20 direct alternating requests. The verifier required integer `Retry-After >= 1` on every 429. The updated Redis-outage check requires exact HTTP 503 and is covered by the latest Java Compose workflow. These are functional checks, not a high-concurrency reject-rate profile.

Docker stats were sampled about every 2 seconds. `peak_container_cpu_pct` in the raw suite output is the busiest individual container (e.g. MySQL reached 111.53% during the Java 200-user profile), not total host CPU. The JSON contains per-container samples. The 8-second Locust profiles are intentionally brief; the zero-sample overview/chat results are retained rather than presented as zero latency or zero error rate.

The Redis middleware lifecycle probe remains separate correctness evidence: FastAPI `TestClient` + the production `RateLimitMiddleware` and an owned Redis 7.4.9 standalone container observed 200/200/429 (`Retry-After: 1000`), Redis-stop 503, then 200/200/429 after a volatile-state-loss restart. It is not an RPS or latency benchmark. The 18 real-Redis Lua tests validate atomic behavior, not service capacity.

Across two nearby CI attempts, the short-run results varied materially. The latest 100-user Python schools run has 66 failures (58.41%) and P50/P95/P99 at the 10s client timeout; the 200-user and overview runs have no completed samples. The earlier run also saw a Python overview P95/P99 of 10s. These failures remain visible and diagnostic, not a pass. The Python public limiter in this standard development Compose stack is in-process, so none of these Python results validates Redis-backed per-IP sharing.

**Still not measured:** limiter-added latency in isolation; Python Industrial Redis per-IP service load; sustained/long-duration production workloads; any managed Redis replica/failover behavior; production hardware, resource limits, data, proxy path, or SLA. Local Docker Engine 29.5.2 / Compose 5.1.4 testing used Redis 7.4.9 on Ubuntu 22.04.5 under WSL2 (20 vCPU, 15.54 GiB host memory) only for the standalone Redis probe, not for the CI service load results.

Before production capacity acceptance, run the limiter scenario against the approved Industrial FastAPI profile with exact `TRUSTED_PROXIES`, and run longer Java/Python profiles at representative low/normal limits. Record actual 200/429/503 counts, concurrency, RPS, limiter and end-to-end percentiles, Redis CPU/memory, resource limits, persistence/topology and recovery. Do not treat these synthetic short-run CI measurements as production capacity or SLA evidence.
