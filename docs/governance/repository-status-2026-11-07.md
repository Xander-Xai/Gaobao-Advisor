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
- Issue #91（OPEN，P2）：Python overview 聚合性能债务。保持开放，需独立、
  可复现、延长窗口 + 受控热身/资源的基线与根因分析后，再定索引/缓存/预聚合
  与性能门槛；本轮无优化 PR，不凭单次短测关闭。

## 6. 分支

- 保留：`master`（受保护）；`feat/java-distributed-services`（开放 PR #89）。
- 本地陈旧：`release/unified-public-gaobao`（无远端对应、无 PR，含早期独有
  提交，未确认所有权/用途，本轮不删除）。

## 7. 面试口径（真实描述）

强调工程实践：LangGraph 状态机、RAG fail-closed、会话安全、MySQL/Redis
Worker 队列、Java 网关分布式限流与故障注入验收。不得虚构生产用户、招生
准确率、性能 SLA 或真实推荐效果；数据边界见 `README.md` 与 `DATA_LICENSE.md`。
