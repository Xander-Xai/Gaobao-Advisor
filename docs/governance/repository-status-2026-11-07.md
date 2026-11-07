# 仓库状态与治理收尾（2026-11-07）

> 基线：`master` `caf7c13` ≈ `origin/master`；PR #89 head `616d71c`（Draft / Owner HOLD，未合并）。
> 本文档只记录可复查的当前状态，不替代 CI 证据。历史审计结论保持不变。

## 1. 技术架构（当前真实关系）

`master`（已交付）：Python FastAPI + LangGraph 单状态图（17 节点）+ Vue 3 SPA +
SQLite 默认 / MySQL 8.4 + Redis 7 + Worker 可选 profile。调用路径：
Browser → Nginx → FastAPI（路由/鉴权/SSE）→ LangGraph（画像→数据查询→RAG→
推理→结构化→质量检查）→ SQLite/MySQL；RAG 默认 BM25 + 合成语料，fail-closed。

功能分支（未合并）：Java 21 + Spring Boot 4.0.9 + Spring Cloud 2025.1.4 网关 +
只读 JDBC Catalog（MySQL `schools`/`admission_scores`/`enrollment_plans` 三表
SELECT-only）。Nginx → Java Gateway：`/api/v1/catalog/**` 走 Java Catalog +
Redis Lua fail-closed 全局桶；其余 `/api/**`（Chat SSE、会话授权、Admin）仍由
Python 权威处理；`/ws/**` 直连 Python。Java overlay 取消 Python API（8000）与
前端（3080）的宿主机映射，默认 SQLite 演示路径不变。

## 2. 功能与验收状态

| 维度 | 结论 |
|---|---|
| Code Complete | `master` 交付；PR #89 实现完整（Java 网关/Catalog/限流/Nginx 双副本） |
| 自动化测试 PASS | PR #89 16/16：CI `42280782634`、CodeQL `42280782558`、Java Services `42280782661`（Java 10 JUnit + live Compose + SSE/HMAC + 数据契约 + Redis/MySQL 故障恢复） |
| 集成验收 PASS（演示口径） | 合成数据：跨网关共享限流（4/20 放行、双实例 429）、Redis/MySQL 停服 fail-closed 与重启恢复、Java/Python 目录契约一致、SSE/HMAC 透传 |
| 性能基准 | **未达标**：`python-overview` 聚合接口曾 50 用户零完成、分页 200 用户 64.9% 失败；最新 8 秒短测（350 请求/0 失败/p95 420ms）短于 10s 客户端超时，变量未受控，不能判瓶颈消除（Issue #91 OPEN） |
| 生产就绪 | **NO-GO**：无 TLS、真实数据授权、专用凭据轮换、跨主机 HA、容量/SLA/隐私审批 |

## 3. 性能测试口径

- 环境：单台 GitHub-hosted runner + Docker Compose；数据：合成 500 学校 /
  12,000 录取行 / 6,000 计划；档位：catalog/python 50–200 用户 8–10s、
  python-overview 50 用户、chat 5/10 用户（无 LLM Key）。
- 历史故障保留：零完成请求（`error_rate_pct`/`p95` 为 `null`，非 0%/100%）、
  100% 失败（`ALL_FAILED`）、缺 `session_id` 的 422 均已链路修复并保留证据；
  最新 chat 5/10 用户 0 失败（20/40 完成请求）仅为短时合成观测。
- 短时合成观测 ≠ 真实容量或 SLA。Methods 见
  [Java/Python 负载基准](../performance/java-python-load.md)；缺陷见 Issue #91。

## 4. 安全与部署（已验证 / 未完成）

- 已验证（合成演示）：Redis 故障 fail-closed（HTTP 503）与恢复、MySQL 只读 +
  `gaobao_reader` 三表 SELECT-only（会话表不可读、UPDATE/CREATE 被拒）、
  SSE 会话所有权 + Bearer 透传、Python 限流 429 与可信代理处理。
- 未完成：TLS/WAF、真实数据授权与隐私审查、Redis ACL/TLS、专用凭据与密钥
  轮换、跨主机 HA、Java 依赖 SCA/SBOM、渗透测试。历史 Git 对象暴露
  （Issue #58）为 owner 接受风险关闭，未 purge。

## 5. PR 与 Issue 治理（2026-11-07）

- PR #89（Draft）：16/16 绿、MERGEABLE CLEAN，但 **Draft / Owner HOLD** 继续有效，
  本轮不提议合并；CI 全绿不等于生产就绪。复核结论见 PR 评论。
- Issue #90：已关闭，CI 门槛与负载观测职责分离（informational 档位 + 零样本
  语义 + chat `session_id` 修复），证据链一致；关闭理由有效（#90 closedAt
  2026-11-06T19:43:08Z，最终 head `616d71c`）。
- Issue #91（CLOSED 2026-11-07，完成）：overview 16→9 次往返批量聚合优化（PR #93
  已合并，`benchmarks/overview_perf_bench.py` 可复现；30s×3 轮前后对照 median
  -45%/RPS +75%，回归测试通过）。多进程 / MySQL 复验不在本轮范围内。

## 5b. PR #89 冲突状态（2026-11-07 晚些时）

- 候选合并已普通 Fast-forward 至原分支（`fc019b0`，含 master `6a7af10`），
  GitHub 显示 MERGEABLE。仍为 Draft / Owner HOLD，未合并。
- 最新 Java Services 的 `java-compose-smoke` 因短窗 NO_COMPLETED_REQUESTS 工件
  两次失败（同一 run 内 python 200 用户 1140 请求/0 失败），属 Runner 争用方差，
  与本次合并无关；功能检查 14/15 全绿。详情见 PR #89 评论。

## 6. 分支

- 保留：`master`（受保护）；`feat/java-distributed-services`（开放 PR #89）。
- 本地陈旧：`release/unified-public-gaobao`（无远端对应、无 PR，含早期独有
  提交，未确认所有权/用途，本轮不删除）。

## 7. 面试口径（真实描述）

强调工程实践：LangGraph 状态机、RAG fail-closed、会话安全、MySQL/Redis
Worker 队列、Java 网关分布式限流与故障注入验收。不得虚构生产用户、招生
准确率、性能 SLA 或真实推荐效果；数据边界见 `README.md` 与 `DATA_LICENSE.md`。

## Dependabot 治理（2026-11-07）

GitHub 曾报 19 条 Open Alerts（2 Critical/5 High/Medium/Low）。经锁文件核查：

- **已修复（17 条，已合并 `f4bb57a` + `c6b287b`）：**
  langgraph 1.2.14、langgraph-sdk 0.4.6、certifi 2026.10.18、urllib3 2.8.0、
  anyio 4.14.2、langsmith 0.8.18、pytest 9.2.0（`pyproject.toml` 新增
  `[tool.uv] constraint-dependencies` 防浮动）、vite/@vitest/mocker patched。
- **残余（2 条，moderate，dev-only）：** `glob`（GHSA-3g3m-2fjr-6pw4）、
  `minimatch`（GHSA-g3hv-75wg-m249），经 `js-beautify` → `@vue/test-utils`
  测试链引入，当前 js-beautify 1.15.x 仍依赖 glob 10.x，无非破坏性修复版本；
  需 `npm audit fix --force` 破坏性升级，不在本轮采用。非运行期/互联网暴露。

## PR #89 最终审查（2026-11-07）

- 合并 master 安全修复（普通 merge，`55d8841`），16/16 CI 全绿，
  MERGEABLE。仍为 Draft / Owner HOLD，未合并。
- 技术验收：演示/Compose 故障注入 PASS；代码合并门槛满足；
  生产门槛（TLS、真实数据、专用凭据、跨主机 HA、SLO）未满足，代码合并不
  代表允许部署生产。
