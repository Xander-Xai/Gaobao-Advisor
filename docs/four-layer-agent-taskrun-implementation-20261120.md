# 四层 Agent TaskRun 工程集成与验收（2026-11-20）

> **实验性实现，非生产部署**：仅冻结的 SYNTHETIC_DEMO 虚构考生数据。PR #110 在 #103→#105→#106→#107→#108→#109 之上；不得跳过依赖顺序合并。项目基于早期 MIT 许可开源底座二次开发。

## 一、架构与边界

```text
Vue Admin → FastAPI Admin RBAC → TaskRun Journal (SQLite CAS)
                 │                   create → PENDING_APPROVAL
                 │                   separate admin → APPROVED
                 └─ deterministic HTTP execution (approved only)
                                  │
                   separate Worker → claim + lease fence
                                  │
                    ┌─────────────┴────────────────┐
                    │                              │
              baseline planner          opt-in Deep Agents runtime
              (no external calls)       (explicit model permission)
                    │                              │
                    └────────── same SQL tools ────┘
                               │
                    Evidence Gate: five source IDs
                               │
              complete / needs_review / failed receipt
                               │
                   TaskRun journal: durable result
```

**入口**：FastAPI 是任务创建与状态查询权威接口；Vue 操作台负责创建/审批/查看；原 Java Catalog/Gateway 继续做可选跨语言边界，不复制 Agent 状态机，也不通过 Java 直接授予模型工具权限。

**业务控制**：本切片选择自定义持久化状态机（不是声称 LangGraph 必须再编排一次）。原高考咨询 LangGraph 路由保持不变；TaskRun journal 通过 SQLite BEGIN IMMEDIATE、maker-checker、version、lease token、retry budget 控制执行。发起者不能自批；未审批不能领取；过期 Worker 不得提交旧结果。

**自主执行**：Deep Agents 只在单独 Worker 且明确允许外部模型时加载。用官方 create_deep_agent / StateBackend / FilesystemMiddleware / Memory-call limit / SKILL.md；LLM 决定下一次业务只读工具，但最后由经过 SQL 校验的 submit_research 收据与服务端 Evidence Gate 决定是否输出历史决策卡。模型自由文本不能写数据库或被当作录取事实。

**工具与沙箱**：仅暴露固定的 major_lookup、admission_lookup、plan_lookup、submit_research、abstain_with_evidence，以及 StateBackend 中的 read_file。注册的 harness profile 显式去掉默认 Shell、文件增删改查（只保留 read_file）、task 子代理和写 Todo 等能力。模型无任意 SQL、外部 HTTP、MCP 或宿主文件访问；自有开发维护脚本仍使用单独的真实 Docker restricted workspace，不向招生研究 Agent 授予这个工具。MCP 是未来跨系统连接可选方案，不为简历关键词强制接入。

## 二、配置与逐步复现

实验开发环境必须满足：

```bash
export APP_ENV=development
export ENABLE_SYNTHETIC_AGENT_EXPERIMENT=true
export ENABLE_OUTBOUND_FETCH=false
export SYNTHETIC_TASKRUN_JOURNAL=/tmp/gaobao-agent-journal.db
# 数据库还需按项目 README 的 seed_demo_data 初始化，且 TaskRun journal 不得与招生 DB 共用同一文件
# 根据现有 Admin 用户配置，准备独立的 operator 与 admin 两个账号。
```

在 Vue 运营工作台 /admin 的「Agent 研究」栏目，选择 engine=deterministic → 创建任务 → 保存 syn_... ID；用另一个 admin 账号审批，再用 operator 发起执行，查看两个学校的 SQL 来源、选科约束和最终回执。可通过下列 API 复验：

```bash
curl -sS -H "Authorization: Bearer $OP_TOKEN" \
  -H 'Content-Type: application/json' -X POST \
  http://localhost:8000/api/v1/admin/agent-taskruns/runs \
  -d '{"engine":"deterministic","idempotency_key":"demo-review-20261120","profile":{"school_a":"北辰示例科技大学（合成）","school_b":"沧澜示例农业大学（合成）","major":"计算机科学与技术（合成）","province":"示例省·甲","score":550,"subject_type":"物理类","year":2025}}'
# 以响应中的 syn_... 作为 RUN_ID；year 使用当前合成数据集定义的 DEMO_YEAR。
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  -X POST http://localhost:8000/api/v1/admin/agent-taskruns/runs/$RUN_ID/approve
curl -sS -H "Authorization: Bearer $OP_TOKEN" \
  -X POST http://localhost:8000/api/v1/admin/agent-taskruns/runs/$RUN_ID/execute
```

**Deep Agents 的 Worker 必须二次显式授权**：先通过相同业务 API 创建 engine=deepagents 并由不同 admin 审批，之后由可信操作员在隔离 Worker 主机手动执行，而不是给网页传 API Key：

```bash
python -m pip install -r requirements-deepagents.txt
# 在 Worker 的私有环境中配置 OPENAI_API_KEY；不要写入 Git、命令行参数或上传的回执。
python -m server.agent.taskrun_worker \
  --journal /tmp/gaobao-agent-journal.db --run-id "$RUN_ID" \
  --model openai:YOUR_MODEL_ID --permit-external-model
```

未经模型调用授权时，不执行上一步，也不产生真实模型运行报告。默认 CI 用 Fake Agent 验证业务工具与接口合同，官方 SDK smoke 只构建受限 Graph，不调用外部模型。

## 三、关键失败与恢复验证

- 没有 operator/admin Token → 401/403；实验未启用、production → 不开放。
- 同一个 idempotency_key + 相同任务返回同一 run_id；不同任务冲突 → 409。
- 创建人自己审批 → 409；审批前执行 → 409；浏览器直接运行 Deep Agents → 409。
- 通过 BEGIN IMMEDIATE 原子领取；租约中重复领取 → 拒绝；租约超时后最多重领一次，过期 Worker 的 lease token 无法覆盖新 Worker 的结果。
- 少一条真实 SQL 记录、选科冲突或来源不完整 → NEEDS_REVIEW 且隐藏完整学校对比，模型不能凭空补齐。
- SDK 默认内置工具是叠加式注册：不能仅因为传入 tools 参数就假设无 Shell/文件读写；此实现使用 StateBackend、显式排除及官方 SDK 构建 smoke 核对配置。
- HTTP 15 秒只是响应 deadline；Worker 租约是简化服务内恢复策略，**不是**真正强制取消运行中的 Python Thread、持久化 Deep Agents 对话 checkpoint 或端到端 exactly-once。

## 四、成果证明 / 不能宣称的内容

适合简历陈述：自己开发了 Vue/FastAPI/Admin RBAC 到持久化任务状态、人工审批与分离 Worker 的工程链路；真实合成 SQL 多步工具和证据引用经 E2E 验收；另有可选实际 Deep Agents SDK 受限工具适配和 Docker 独立沙箱测试。

**不能陈述**：真实模型 autonomous success rate、真实 Token 降本、Deep Agents 线上部署、真实考生建议准确性、完整分布式耐久执行、通用 MCP 接入、Claude Code/Pi/DSH 已投入该项目。所有这些在没有实跑数据前为 NOT_MEASURED/NOT_IMPLEMENTED。

**工程证据**：tests/test_taskrun_store.py、tests/test_agent_taskrun_api.py、tests/test_taskrun_real_http_e2e.py、tests/test_deepagents_adapter_contract.py、tests/test_deepagents_sdk_smoke.py、frontend/src/components/admin/__tests__/AdminTaskRunPanel.spec.ts。最终 CI 与源码 SHA 以 PR #110 的最新成功记录为准。
