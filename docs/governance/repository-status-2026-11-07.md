# 仓库状态与治理收尾（2026-11-07）

> **本文件是仓库状态的权威说明位置。** 其他文档只描述本仓库自身的技术事实，
> PR/Issue/分支等易过期的 GitHub 状态一律以本文为准并从本文链接。
> 文档只记录可复查的事实，不替代 CI 证据；历史审计结论保持不变。

- 基线：`master` `1d1ff02`（PR #89 与 #101 已合并）
- Open PR：0 · Open Issues：0 · Dependabot Open Alerts：0
- 远程分支：`master`；本地历史分支 `release/unified-public-gaobao`（保留）

## 1. 技术架构（当前真实关系）

`master`（已交付）：Python FastAPI + LangGraph 单状态图（17 节点）+ Vue 3 SPA +
SQLite 默认 / MySQL 8.4 + Redis 7 + Worker 可选 profile。调用路径：
Browser → Nginx → FastAPI（路由/鉴权/SSE）→ LangGraph（画像→数据查询→RAG→
推理→结构化→质量检查）→ SQLite/MySQL；RAG 默认 BM25 + 合成语料，fail-closed。

可选 Java overlay（已随 PR #89 合入 `master`，Squash `7c87686`）：Java 21 +
Spring Boot 4.0.9 + Spring Cloud 2025.1.4 网关 + 只读 JDBC Catalog（MySQL
`schools`/`admission_scores`/`enrollment_plans` 三表 SELECT-only）。Nginx →
Java Gateway：`/api/v1/catalog/**` 走 Java Catalog + Redis Lua fail-closed
全局桶；其余 `/api/**`（Chat SSE、会话授权、Admin）仍由 Python 权威处理；
`/ws/**` 直连 Python。Java overlay 取消 Python API（8000）与前端（3080）的
宿主机映射，默认 SQLite 演示路径不变。

## 2. 功能与验收状态

| 维度 | 结论 |
|---|---|
| Code Complete | `master` 交付；Java 网关/Catalog/限流/Nginx 双副本实现完整 |
| 自动化测试 PASS | PR #89 合并前后均 16/16：CI [42372475136](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/42372475136)、CodeQL [42372475181](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/42372475181)（含 `java-kotlin`）、Java Services [42372475149](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/42372475149)；合并后主分支再验 [CI 42382566343](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/42382566343)、[Java Services 42376544386](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/42376544386) |
| 集成验收 PASS（演示口径） | 合成数据：跨网关共享限流（4/20 放行、双实例 429）、Redis/MySQL 停服 fail-closed 与重启恢复、Java/Python 目录契约一致、SSE/HMAC 透传 |
| 性能基准 | **部分达标**：`python-overview` 聚合由 16 次往返降为 9 次（PR #93），仓库内基准同配置 50 并发 median -45%、RPS +75%；短时合成观测仍不等于容量或 SLA（Issue #91 已关闭，残余复验见性能文档） |
| 生产就绪 | **NO-GO**：无 TLS、真实数据授权、专用凭据轮换、跨主机 HA、容量/SLA/隐私审批 |

## 3. 性能测试口径

- 环境：单台 GitHub-hosted runner + Docker Compose；数据：合成 500 学校 /
  12,000 录取行 / 6,000 计划；档位：catalog/python 50–200 用户 8–10s、
  python-overview 50 用户、chat 5/10 用户（无 LLM Key）。
- 历史故障保留：零完成请求（`error_rate_pct`/`p95` 为 `null`，非 0%/100%）、
  100% 失败（`ALL_FAILED`）、缺 `session_id` 的 422 均已链路修复并保留证据。
- 短时合成观测 ≠ 真实容量或 SLA。方法、环境与前后对照见
  [Java/Python 负载基准](../performance/java-python-load.md)。

## 4. 安全与部署（已验证 / 未完成）

- 已验证（合成演示）：Redis 故障 fail-closed（HTTP 503）与恢复、MySQL 只读 +
  `gaobao_reader` 三表 SELECT-only（会话表不可读、UPDATE/CREATE 被拒）、
  SSE 会话所有权 + Bearer 透传、Python 限流 429 与可信代理处理。
- 未完成：TLS/WAF、真实数据授权与隐私审查、Redis ACL/TLS、专用凭据与密钥
  轮换、跨主机 HA、Java 依赖 SCA/SBOM、渗透测试。历史 Git 对象暴露
  （Issue #58）为 owner 接受风险关闭，未 purge。
- 依赖治理：Dependabot 19 条告警已全部修复并复扫为 0（见第 7 节）；
  0 告警不等于系统无安全风险，生产 NO-GO 条件依然成立。

## 5. PR 与 Issue 治理

| 对象 | 结论 | 证据 |
|---|---|---|
| PR #89 | 已合并（Squash `7c87686`）。合并前经历：Owner HOLD、冲突无损修复（`da2f219`）、HA 探针有界重试（`860498e`）与就绪门（`558370f`） | [PR #89](https://github.com/Xander-Xai/Gaobao-advisor/pull/89) |
| PR #92–#101 | 文档同步、依赖安全修复（`f4bb57a`/`c6b287b`/`ff02ded`）、性能优化（`6a7af10`）均已合并 | Actions / Commit 记录 |
| Issue #90 | 已关闭：CI 门槛与负载观测职责分离（informational 档位 + 零样本语义 + chat `session_id` 修复），证据链一致 | [Issue #90](https://github.com/Xander-Xai/Gaobao-Advisor/issues/90) |
| Issue #91 | 已关闭（完成）：overview 16→9 次往返批量聚合优化 + 仓库内可复现基准 + 回归测试；多进程 / MySQL 复验不在该 Issue 范围 | [Issue #91](https://github.com/Xander-Xai/Gaobao-Advisor/issues/91) |

## 6. 分支

- `master`：受保护，唯一远程分支。
- `release/unified-public-gaobao`：**仅本地**，245 个独有历史提交，无 PR、无
  worktree 引用；已用 Git Bundle 本地备份并验证可恢复。按 owner 决策保留，
  不推送、不改写、不删除。
- `feat/java-distributed-services` 已在 PR #89 合并后删除（内容已进入
  `master`，无独有工作）。

## 7. 面试口径（真实描述）

强调工程实践：LangGraph 状态机、RAG fail-closed、会话安全、MySQL/Redis
Worker 队列、Java 网关分布式限流与故障注入验收。不得虚构生产用户、招生
准确率、性能 SLA 或真实推荐效果；数据边界见 `README.md` 与 `DATA_LICENSE.md`。

## 8. 依赖安全治理（2026-11-07）

GitHub 曾报 19 条 Open Alerts（2 Critical / 5 High / 其余 Medium-Low）。
经锁文件核查后全部修复，复扫为 **0 open**：

- Python（`f4bb57a`、`c6b287b`）：langgraph 1.2.14、langgraph-sdk 0.4.6、
  certifi 2026.10.18、urllib3 2.8.0、anyio 4.14.2、langsmith 0.8.18、
  pytest 9.2.0。`pyproject.toml` 新增 `[tool.uv] constraint-dependencies` 作为
  安全下限，防止再生成锁文件时浮动回漏洞区间。
- 前端（`ff02ded`）：vite / @vitest/mocker / js-beautify 升级，并用
  `npm overrides` 将 `glob` 钉到 14.0.2、`minimatch` 钉到 11.0.5，
  覆盖 GHSA-3g3m-2fjr-6pw4 与 GHSA-g3hv-75wg-m249（非破坏性，未使用
  `npm audit fix --force`）。
- 运行时集合复验：`pip-audit --requirement requirements.lock` → "No known
  vulnerabilities found"。

## 9. 合并前快照（历史记录，2026-11-07 早）

以下为 PR #89 合并前的状态记录，保留以便追溯当时的判断；**当前状态以上文为准**：

- 当时基线：`master` `caf7c13`；PR #89 head `616d71c`（Draft / Owner HOLD）。
- 冲突修复候选分支 `resolve/pr89-conflicts`（`da2f219`）以普通 Fast-forward
  方式并入原功能分支，未改写历史。
- `java-compose-smoke` 曾因短窗 `NO_COMPLETED_REQUESTS` 工件失败：同一 run 内
  python 200 用户 1140 请求 / 0 失败，属共享 Runner 争用方差而非功能回归；
  修复为探针有界重试 + 全栈就绪门，功能门槛未放宽。
