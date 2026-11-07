> **Public-product addendum (2026-10-31):** the public README, data explorer and architecture release is recorded in [2026-10-31-public-product-release.md](2026-10-31-public-product-release.md). Issue #58 remains OPEN / ACCEPTED_RISK at this snapshot; the historical object is not claimed as purged or licensed.
>
> **Status update (2026-11-07):** Issue #58 was closed on GitHub as **CLOSED / NOT_PLANNED / ACCEPTED_RISK** by owner disposition (2026-10-31 13:50 UTC); historical objects/PR refs were not purged and redistribution authorization is not established — closure is administrative risk acceptance, not remediation.

# gaobao-advisor 最终治理与可靠性验收报告

- **日期**：2026-10-31（Asia/Shanghai）
- **仓库**：`Xander-Xai/gaobao-advisor`（public，default `master`）
- **代码基线**：`0c7ff79`（Issue #58 Phase A–D 防泄露控制合并后；本文档合并后以 `git log -1 -- docs/audits/2026-10-31-gaobao-final-governance.md` 为准）
- **执行环境**：Linux/WSL2，Python 3.11.15，MySQL 8.4，Redis 7，Docker 29.5.2，Node 20.20.2
- **权限**：仓库 `admin=true`；未执行 force-push、历史重写、数据库破坏性操作或仓库可见性修改。
- **索引**：[repository-audit.md](../governance/repository-audit.md)

> 说明：审计开始时本地 HEAD 为 PR #52 分支 `0144fe3`，`master` 为 `68f12a7`。审计过程中，PR #52/#53 被并发合并（`52b96a5` / `a69a6ed`）。本报告以实时读取和重新执行的结果为准，未复用审计前的“0 Open PR”等快照。
>
> **Issue #58 专项更新（2026-10-31 晚）：** 历史暴露的只读取证、数据分类、隔离备份、
> 沙盒重写和防泄露控制已完成，但远端 PR refs / 缓存 / 不可达对象仍需 owner 授权和
> GitHub Support 处理。详见 [gaobao-issue-58-remediation.md](gaobao-issue-58-remediation.md)。
> 因此 `SECURITY_REVIEW` 与 `REPOSITORY_READY` 仍为 **BLOCKED**，`ISSUE_58_RESOLVED`
> 为 **BLOCKED**；不得将沙盒成功或当前树干净表述为历史已清除。
>
> **Support-first 复核（2026-10-31 晚）：** `master@44ae6a1` 与唯一 tag 均无敏感对象；
> 17 个敏感 commit 全部只通过 14 个 PR refs 可达，当前 forks=0，满足 GitHub 官方
> “先清理非 PR 引用、再由 Support 处理 PR/缓存”的前提。**不需要 force push master**；
> 应申请 Support 删除/取消引用 PR refs、执行服务端 GC 并清理缓存。Support 工单
> 尚未提交（无授权/无门户访问），`ISSUE_58_RESOLVED` 仍为 BLOCKED。

---

## 0. 最终结论摘要

| 维度 | 状态 | 说明 |
|---|---|---|
| 代码整改 | **PASS** | Issue #54/#55/#70 的 P0/P1 缺陷已修复并有回归测试 |
| Issue #40 | **PASS** | 独立复验 34 个适用故障场景 + 负载 + 生产默认恢复；结论 `KEEP_LIST` |
| PR 治理 | **PASS** | 本轮合并 #56/#57/#69/#71/#75；关闭依赖噪声 PR；最终 0 Open PR |
| 分支治理 | **PASS** | 远端仅 `master`；合并后工作分支已删除；本地 `release/unified-public-gaobao` 保留待取证 |
| Issue 治理 | **PASS** | 已关闭 #40/#54/#55/#70；#58 作为未解决安全事件保持 Open |
| CI 健康 | **PASS** | `master@0c7ff79` CI（run 41198100431）与 CodeQL（run 41198100449）success；Full Compose E2E 通过 |
| 安全审查 | **BLOCKED** | 历史 Git 对象仍暴露已删除的 72 MiB 数据库/用户消息（Issue #58） |
| 主分支集成 | **PASS** | 所有本轮修复均已进入 `master` |
| 仓库可交付 | **BLOCKED** | 历史数据暴露未清除前，不宣称 `REPOSITORY_READY` |

---

## A. 问题整改矩阵

| # | 问题 | 严重级别 | 根因 | 修复位置 | 测试证据 | 状态 |
|---|---|---|---|---|---|---|
| A1 | 认证会话的聊天不加载已持久化画像；`_query_state` 每轮重置 | P0 | `/chat` 只用客户端 `slots`，从不读 DB；`_query_state` 不是 LangGraph channel | `server/routes/chat.py`, `server/graph/state.py`, `server/graph/nodes/question.py`, `server/graph/nodes/memory.py` | `tests/test_chat_state_structured.py::test_authenticated_chat_loads_persisted_profile`, `::test_chat_persists_advanced_query_state` | **FIXED** |
| A2 | 聊天写入扁平 slots，`GET /profile` 读取嵌套 slots，聊天后画像被清空 | P0 | `Conversation.slots_json` 存在两种不兼容格式 | `slots/persistence.py`, `server/user_profile.py`, `server/graph/nodes/memory.py` | `::test_storage_round_trip_produces_a_readable_profile`, `::test_legacy_flat_rows_are_readable`, `tests/test_profile_save_slots.py` | **FIXED** |
| A3 | 用户明确纠正（`我改成620分`、改专业）被旧值覆盖 | P0 | `slot_extract_node` 无条件“existing wins” | `slots/extractor.py`, `server/graph/nodes/extract.py` | `::test_explicit_score_correction_replaces_stale_value`, `::test_new_interest_replaces_old_negative_preference`, `::test_passing_mention_does_not_overwrite_profile` | **FIXED** |
| A4 | 后端发送 `structured` 事件，前端完全不消费，结构化推荐卡片不可见 | P0 | Vue store 无 `structured` 分支，无卡片组件 | `frontend/src/stores/chat.js`, `frontend/src/components/chat/StructuredCard.vue`, `MessageBubble.vue` | `tests/.../chat.test.js`（structured 缓冲）、`StructuredCard.spec.js`（92 前端测试） | **FIXED** |
| A5 | 无 LLM Key 时模板回复只输出标题，不列推荐院校 | P1 | `render_reply_node` 读取不存在的 `structured.matched_schools` | `server/graph/nodes/render.py` | `::test_template_reply_lists_matched_schools_and_sources` | **FIXED** |
| A6 | `/data/scores`、`/data/plans` 非法 cursor 返回 500；`/data/schools` 静默空页 | P1 | `int(cursor)` 未校验；整数与字符串比较 | `server/routes/data.py` | `tests/test_data_cursor_validation.py`（11 项） | **FIXED** |
| A7 | 公共限流超限返回 500 而非 429；代理后所有用户共享代理 IP | P1 | Starlette middleware 中抛 `HTTPException`；使用 `request.client.host` | `server/middleware/ratelimit.py`, `server/admin/client_ip.py` | `tests/test_middleware_ratelimit.py`（80 请求实测 53×200 + 27×429） | **FIXED** |
| A8 | `data_profile` 硬编码 `SYNTHETIC_DEMO`，导入真实数据后仍谎报合成 | P1（合规） | 健康检查/后台统计使用常量 | `server/services/data_profile.py`, `server/routes/health.py`, `server/admin/repository.py` | `tests/test_data_profile.py`（EMPTY/SYNTHETIC/CUSTOM/MIXED/UNKNOWN） | **FIXED** |
| A9 | Sentry 测试用假 DSN 初始化真实 transport，发出网络事件 | P2 | 测试未 mock `sentry_sdk.init` | `tests/server/test_monitoring.py` | 全量测试不再出现 “Sentry is attempting to send …” | **FIXED** |
| A10 | CodeQL 新增 12 个 high 告警（正则/敏感日志） | P1/P2 | 邮箱正则无界、HTML 清理正则不完整、脚本生成本地凭据 | `server/admin/privacy.py`, `server/middleware/security.py`, `server/middleware/csp.py`, `legacy/utils.py` | CodeQL 复跑：0 open；8 条按逐条理由 dismissed | **FIXED / TRIAGED** |
| A11 | 历史提交仍暴露 72 MiB 招生库与 3,014 条用户消息 | **P0（安全/合规）** | 删除工作区文件不等于删除 Git 对象；需历史重写 + GitHub Support | 当前树已无该文件；无法在无授权下重写公开历史 | 只读 SHA/大小/计数证据；公开下载 HTTP 200 | **BLOCKED**（Issue #58） |
| A12 | 离线 LLM-as-Judge 以 50 分“中性分”落库并可能显示 fail | P2（残余） | 未配置 Key 时全部维度 unscored，但聚合仍为 50 | 未在本轮修改 | 已记录为残余风险，避免错误标注 | **OPEN / 非阻断** |
| A13 | 匿名 session token 无过期时间/撤销机制 | P3（设计风险） | HMAC(session_id) 能力令牌设计 | 未在本轮修改 | 安全边界已文档化；不宣称生产级 IAM | **ACCEPTED RISK** |

### 已验证为 NOT_REPRODUCED / ALREADY_FIXED 的高风险项

| 检查 | 结论 | 证据 |
|---|---|---|
| MySQL 干净环境迁移 + seed + 幂等 | **PASS** | `tests/test_mysql_migrations.py`：fresh upgrade、adopt-existing、index drift 3/3；33 项真实 MySQL/Redis 集成测试 |
| Outbox / BRPOP / 租约 / fencing / 重复投递 | **PASS** | `tests/test_worker_integration.py` 5/5、`test_queue_recovery.py` 6/6；独立故障矩阵 34/34 |
| 双 Worker 并发、旧 Worker 提交 | **PASS** | 独立故障矩阵 `two_recoverers`、`after_claim`、`during_execute`；0 duplicate business executions |
| RBAC / 限流 / Token 撤销 / 可信代理 | **PASS** | `tests/test_admin_security.py` 20/20、`test_admin_limits_redis.py` 11/11 |
| RAG 语料完整性 / fail-closed / 无证据区分 | **PASS** | `test_rag_integrity.py`, `test_rag_demo_e2e.py`, `test_g9_retrieval.py` |
| SSE 增量解析 / 断流 / 跨会话隔离 | **PASS** | `test_chat_sse.py`, `frontend/.../chatStreaming.test.js` |
| SQL major 过滤 / 正常 cursor 分页 | **PASS** | `test_data_scores_major_filter.py`, `test_data_cursor_validation.py` |

---

## B. Issue #40 技术决策

- ADR：[ADR-009](../superpowers/adr/ADR-009-redis-delivery-selection.md)
- 报告：[redis-queue-recovery.md](../performance/redis-queue-recovery.md)
- 原始数据：`docs/performance/results/queue-recovery-final.json`（172 次故障运行）
- 独立复验原始数据：`/tmp/queue-recovery-independent-fault.json`、`...-load.json`、`...-default.json`（本次审计生成，未入库）

### B.1 候选方案对比

| 维度 | List + Outbox（现状） | BLMOVE processing list | Streams consumer group |
|---|---|---|---|
| 交接 | `LPUSH`/`BRPOP`，立即移除 | 原子移入 processing list | `XREADGROUP` 创建 PEL |
| 显式 ACK | 无 Redis ACK；DB claim/Outbox 为准 | 处理完移除 processing 项 | `XACK` |
| Worker 崩溃恢复 | 60s 投递租约 + 30s 扫描；600s 运行租约 | processing lease + 扫描 | `XAUTOCLAIM`/`XPENDING` |
| 重复投递 | 可能；由原子 claim + fencing 吸收 | 可能；同左 | 可能；同左 |
| 恢复延迟（4s 实验租约） | p50 2.615s / p95 3.884s / p99 4.301s | 2.338 / 6.459 / 6.471s | 2.328 / 6.598 / 6.720s |
| 生产默认恢复 | **60.56–62.10s**（本次独立复验 60.77s） | 未验证 | 未验证 |
| 吞吐（100 jobs/4 workers） | 24.49 jobs/s（独立复验 26.69） | 22.51（独立 22.37） | 25.94（独立 21.52） |
| 运维复杂度 | 低 | 中 | 高（PEL/consumer/trim） |
| 迁移/回滚成本 | 0 | 中 | 高 |
| 结论 | **KEEP_LIST** | 未来若需 <60s SLO 的首选 | 仅在需要 consumer 协调/PEL 时 |

### B.2 独立故障注入复验（2026-10-31）

环境：专用 `gaobao-qr` Compose（MySQL 8.4 + Redis 7 AOF，独立 schema/端口），4s delivery/running lease + 1s scan；真实子进程 SIGKILL。

| Scheme | 适用场景 | 结果 | 重复业务执行 | 丢任务 | 错误终态 |
|---|---:|---|---:|---:|---:|
| List + Outbox | 10 | 10 PASS（另 2 个 N/A） | 0 | 0 | 0 |
| BLMOVE | 12 | 12 PASS | 0 | 0 | 0 |
| Streams | 12 | 12 PASS | 0 | 0 | 0 |

- 独立恢复延迟（PASS 样本）：List p50/p95/p99 = 2.935/3.901/3.901s；BLMOVE 2.255/6.559/6.559s；Streams 2.319/6.554/6.554s。
- 独立负载 20/1、50/2、100/4 各 1 次：List 6.09/13.571/26.692 jobs/s；BLMOVE 5.725/12.711/22.371；Streams 6.363/11.994/21.519；0 violations。
- 生产默认 List（60s/30s/600s）1 次：**60.7713s PASS**，与 PR #52 的 60.56–62.10s 一致。
- 原 PR #52 证据：172 次运行，List 50 PASS + 2 N/A，BLMOVE 60 PASS，Streams 60 PASS；0 重复业务执行/丢任务/错误终态。

### B.3 决策

**`KEEP_LIST`**。数据库是状态真源，重复投递由原子 claim + attempts fencing + 唯一 issue 约束吸收；当前没有生产恢复 SLO 证明必须引入 Streams/BLMOVE。60s 不是严格上界，已在 ADR 中纠正。若未来定义 <60s p95 SLO，先评估 BLMOVE；不声称 exactly-once。

Issue #40 已按上述证据关闭；其验收标准（方案对比、真实故障注入、恢复延迟、重复率、ADR）均满足。

---

## C. PR 治理台账

| PR | 标题/范围 | 状态 | 合并 SHA / 处理 | CI |
|---|---|---|---|---|
| #52 | Redis List/BLMOVE/Streams 恢复对比 | MERGED（审计期间并发合并） | `52b96a5` | 10/10 |
| #53 | 队列恢复治理台账 | MERGED（并发） | `a69a6ed` | 10/10 |
| #56 | 聊天画像状态 + 结构化卡片 + cursor + 数据画像 | **MERGED（本轮）** | `ce04b0c` | 10/10 run 41188008931 |
| #57 | Dependabot 配置 | **MERGED（本轮）** | `7a27877` | 10/10（更新分支后 run 41189518708） |
| #69 | CodeQL + gitleaks 校验 + Dependabot 限额 | **MERGED（本轮）** | `31465c0` | 10/10 + CodeQL run 41190378777 |
| #71 | 公共限流 429 + 可信代理 | **MERGED（本轮）** | `26fdaa9` | 10/10 run 41190833532 |
| #75 | CodeQL 正则修复 + 历史暴露声明 | **MERGED（本轮）** | `a56d38c` | 10/10 + CodeQL run 41191949093 |
| #59–#68 | Dependabot 版本更新（Python/Node/Actions/Docker） | CLOSED | 不合并；锁定文件需整体再生成 | 部分失败/未完成 |
| #72–#74 | Dependabot 后续版本更新 | CLOSED | 同上 | 未完成 |
| #5, #3 | 历史未合并 PR | CLOSED | 无独有资产需合并 | — |

最终 Open PR：**0**（`.github/dependabot.yml` 已将版本更新限制为 0；安全更新仍启用）。

---

## D. Issue 治理台账

| Issue | 状态 | 证据/处置 |
|---|---|---|
| #40 reliability: consumer-group ACK | **CLOSED** | PR #52 + ADR-009 + 本报告 B 节独立复验 |
| #54 P0 chat state / structured | **CLOSED** | PR #56，`tests/test_chat_state_structured.py` |
| #55 P1 cursor 500 | **CLOSED** | PR #56，`tests/test_data_cursor_validation.py` |
| #70 P1 rate limit 500 | **CLOSED** | PR #71，`tests/test_middleware_ratelimit.py` |
| #58 historical DB exposure | **OPEN / BLOCKED** | 需 owner 授权历史重写 + GitHub Support；当前树干净不等于历史清除 |
| #36/#37/#38/#39 | CLOSED（历史） | 迁移、Full Compose、基准、分布式安全；本轮集成/CI 再次验证 |
| #10/#22/#24 | CLOSED（历史） | 会话令牌、版本、合成 E2E；全量测试回归通过 |
| #59–#74 | CLOSED（PR） | Dependabot 噪声，非 Issue |

Issue 标签使用 `bug`、`security`、`reliability`、`priority:p1` 等；#58 保持 `security` 标签。

---

## E. Branch 治理台账

| 分支 | 位置 | HEAD | 处置 | 依据 |
|---|---|---|---|---|
| `master` | remote/local | `0c7ff79`（Issue #58 报告合并前） | **保留** | 默认分支，受保护 |
| `release/unified-public-gaobao` | local only | `858df37` | **保留，禁止推送/删除** | 与 master 无共同 merge-base；含独有 316 个提交及历史 72 MiB DB 对象，取证/清理前不得删 |
| `test/redis-queue-recovery-comparison` | remote（已删） | `0144fe3` | 已随 #52 合并删除 | 无独有资产 |
| `fix/p0-chat-state-structured` | remote（已删） | — | 已随 #56 合并删除 | 无独有资产 |
| `ci/security-governance-hardening` | remote（已删） | — | 已随 #57 合并删除 | 无独有资产 |
| `ci/codeql-and-workflow-hygiene` | remote（已删） | — | 已随 #69 合并删除 | 无独有资产 |
| `fix/public-ratelimit-429` | remote（已删） | — | 已随 #71 合并删除 | 无独有资产 |
| `fix/codeql-triage-and-docs` | remote（已删） | — | 已随 #75 合并删除 | 无独有资产 |
| `dependabot/*` | remote（已删） | — | 关闭后删除 | 纯依赖升级，无独有资产 |

未执行 force-push、未重写历史、未删除含独有资产的分支。GitHub PR refs 仍可能保留旧对象；见 #58。

---

## F. CI 与安全验收

### F.1 实际执行命令与结果

| 命令/检查 | 结果 | 环境 |
|---|---|---|
| `.venv/bin/python -m pytest tests/ --tb=short -q` | **1177 passed / 34 skipped**；CI 覆盖率门禁通过（新增 guard 前本地实测 81.76%） | Python 3.11.15 |
| `MYSQL_TEST_URL=... REDIS_TEST_URL=... pytest <integration files>` | **33 passed**（MySQL 8.4 / Redis 7） | 专用 `gaobao-audit-*` 容器 |
| `cd frontend && npm test -- --run` | **15 files / 92 tests passed**（vue-tsc + vitest） | Node 20.20.2 |
| `cd frontend && npm run build` | Vite build passed | Node 20 |
| `ruff check .` / `ruff format --check .` | passed（294 files） | Ruff 0.16.x |
| 独立队列故障矩阵 | 34/34 applicable PASS；0 violations | 专用 `gaobao-qr` Compose |
| 独立队列负载 | 9/9 PASS；0 violations | 同上 |
| 生产默认 List 恢复 | **60.7713s PASS** | 60/30/600 lease |
| `master@0c7ff79` CI | **success**（run 41198100431） | GitHub Actions |
| `master@0c7ff79` CodeQL | **success**（run 41198100449） | GitHub Actions |
| Full Compose E2E | 每次 PR + master 均 success（Nginx → API → MySQL/Redis/Worker → 审核 → 审计 → Token 撤销） | GitHub Actions |

### F.2 GitHub 安全状态（实时读取）

| 项 | 状态 |
|---|---|
| Secret scanning | **enabled** |
| Secret scanning push protection | **enabled** |
| Dependabot security updates | **enabled** |
| Dependabot alerts | **0 open** |
| Secret scanning alerts | **0 open**；历史 1 条 Slack 占位符已 `false_positive` 关闭 |
| Code scanning (CodeQL) | **0 open**；8 条 `clear-text` 告警逐条 dismissed（本地 0600 凭据生成/测试报告标签/演示输出），理由已写入 GitHub |
| Branch protection | 13 required checks（原 10 + `Analyze (python)` / `Analyze (javascript-typescript)` / `CodeQL`），strict、enforce admins、no force/delete、conversation resolution |
| Actions 权限 | 顶层 `contents: read`；CodeQL 单独 `security-events: write` |
| gitleaks 供应链 | v8.28.0 tarball 增加 SHA-256 校验与下载重试（PR #69） |

### F.3 未解决风险

1. **历史数据暴露（#58，最高优先级）**：`data/gaokao.db` 的 75,018,240 字节对象仍可通过公开历史 commit `5278ae1` 获取；含 3,016 校、357,494 条分数、3,014 条用户消息；公开下载已实测 HTTP 200。当前树无该文件，但历史未清除。
2. **GitHub Support #4849701**：本环境无访问权限，无法确认进展；不能把 Issue 关闭或 README 修改当作已清除。
3. 会话 Token 无过期/撤销（匿名能力令牌），仅适合 demo；生产需重新设计。
4. 离线 LLM Judge 的中性 50 分仍会落库；未在本轮修改，避免将其展示为真实质量。
5. 基准为合成负载、单机 WSL2/Docker；不是生产 SLA/容量结论。
6. `release/unified-public-gaobao` 本地分支仍含敏感历史对象，禁止推送。

---

## G. 最终验收状态

| 状态项 | 结果 |
|---|---|
| `CODE_REMEDIATION` | **PASS** |
| `ISSUE_40_RESOLUTION` | **PASS** |
| `PR_GOVERNANCE` | **PASS** |
| `BRANCH_GOVERNANCE` | **PASS** |
| `ISSUE_GOVERNANCE` | **PASS** |
| `CI_HEALTH` | **PASS** |
| `SECURITY_REVIEW` | **BLOCKED**（历史数据暴露 #58；Phase A–D 已完成，远端清理未执行） |
| `MASTER_INTEGRATION` | **PASS** |
| `REPOSITORY_READY` | **BLOCKED**（安全项未闭环） |

> Issue #58 专项状态：`SENSITIVE_DATA_CLASSIFIED` PASS；`EXPOSED_OBJECTS_INVENTORIED`
> PASS；`PRIVATE_BACKUP_VERIFIED` PASS；`LOCAL_UNIQUE_BRANCH_PRESERVED` PASS；
> `SANDBOX_REWRITE_VERIFIED` PASS（方法降级）；`OWNER_REMOTE_AUTHORIZATION` BLOCKED；
> `REMOTE_HISTORY_REWRITTEN` NO/BLOCKED；`GITHUB_PR_REFS_CLEANED` BLOCKED；
> `GITHUB_CACHE_PURGED` BLOCKED；`SUPPORT_CASE_VERIFIED` BLOCKED；
> `FORK_RESIDUAL_RISK_REVIEWED` PASS；`PREVENTION_CONTROLS_ENABLED` PASS（PR #77）；
> `ISSUE_58_RESOLVED` BLOCKED。

---

## H. 最终问答

1. **当前 Open PR？** 0。
2. **当前 Open Issue？** 1：**#58**（历史数据暴露，BLOCKED）。
3. **当前 Branch？** 远端仅 `master`；本地 `master` 与保留的 `release/unified-public-gaobao`（禁止推送/删除）。
4. **Issue #40 是否真正解决？** 是。独立复验 34/34 适用故障场景、9/9 负载、默认 60.77s 恢复；决策 `KEEP_LIST`，不声称 exactly-once。
5. **master 最新 SHA？** Issue #58 专项报告合并前为 `0c7ff79`；本报告合并提交以 `git log -1 -- docs/audits/2026-10-31-gaobao-final-governance.md` 为准。
6. **CI 是否全部通过？** 是。`master@0c7ff79` CI（41198100431）+ CodeQL（41198100449）全绿；13 项 required checks 生效。
7. **是否仍有安全/版权风险？** 有。历史 72 MiB 数据库/用户消息仍公开可下载（#58）；当前树无第三方真实数据/凭据，MIT 归属与合成数据许可已记录。
8. **能否在全新环境可靠运行？** 合成 Demo 可以：MySQL 干净迁移、seed、Compose 冷启动、健康检查、API/前端/Worker 链路均已通过。真实数据/生产 SLA 未验证。
9. **未完成 P0/P1？** **#58** 是唯一未闭环的 P0 安全项；A12/A13 为已记录的非阻断残余风险。
10. **下一阶段最有价值的三个工程任务？**
    1. 在 owner 授权下完成历史重写/对象清除并与 GitHub Support 验证（#58），同时评估用户通知与数据授权。
    2. 为匿名会话引入可过期/可撤销令牌，并将公共限流改为跨进程/Redis 后端（当前仅单进程 + 可信代理）。
    3. 建立依赖维护窗口：整体再生成 `requirements.lock`，跑 `pip-audit`/`npm audit`，并将 CodeQL 告警处理纳入每次 PR 门禁。

---

## I. 交付物与回滚

- 代码修复：PR #56/#71/#75；CI/安全：PR #57/#69。
- 队列证据：`docs/performance/redis-queue-recovery.md`、`docs/performance/results/queue-recovery-final.json`、ADR-009。
- 治理台账：`docs/governance/repository-audit.md`、本文件。
- 安全事件：#58（保持 Open，附脱敏证据与处置方案）。
- 回滚：本轮均为常规 squash merge；回滚单 PR 使用对应 squash SHA 的 `git revert`。**不得**通过 force-push/历史重写回滚；#58 的清理必须在独立授权流程中进行。
