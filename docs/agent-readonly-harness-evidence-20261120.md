# Gaobao Advisor：Agent Harness 工程化改造证据手册（2026-11-20）

> **历史状态快照（2026-11-20）**：当时 PR #105 待合并；此模块独立于现有 FastAPI 聊天主链路。仅用于 `SYNTHETIC_DEMO` 演示数据库和合成知识语料。PR #103 和 Issue #104 为当时的关联记录；实时状态以 GitHub 为准。

## 1. 业务目标先于框架

目标不是把 17 节点 LangGraph 图改名为 Harness。高考志愿业务有明确的规则约束：数据必须有来源、推荐与事实分开、数据缺失拒绝冒充事实、关键流程保持确定性。只对**只读的院校/专业/知识探索**开放模型选择工具，其他业务控制仍由原 LangGraph 主链路承担。

### 架构边界

```text
现有业务入口（未更改）
Vue3 → FastAPI/SSE → 17 节点 LangGraph → SQL/BM25 → 来源与风险校验
                                  │
实验性独立侧车（不在当前线上路由中）
task → Planner(脚本离线 / 显式注入真实模型) → Bounded Agent Loop
                                          │
              allowlisted 只读工具 + Schema + Source Gate
                                          │
           真实合成 SQLite / 既有合成 BM25 服务
                                          │
      结构化 Trace → 30-case 独立预期 → 脱敏 Eval 报告
```

- Loop: `server/agent/readonly_harness.py`；Planner 每步决定 tool / finish / abstain。工具只按固定 Schema 接收参数，不通过模型文本组装 SQL 或 shell。
- Runtime: 显式模型步数上限（8）、真实工具调用上限（5）、同参重复调用上限（2）、观察内容裁剪（800 字符）、Planner 上下文长度上限（2400 字符）。
- Tool 实现：`server/agent/readonly_tools.py`。由原项目服务读取数据库与合成知识库，验证单独 `SYNTHETIC_DEMO` SQLite 与语料身份；不注册写操作或外网操作。
- Evidence: 模型 `finish` 必须携带当前执行过程实际观察到的合成来源 ID；拒绝虚构引用和没有证据的“成功”。**来源 ID 存在并不等于语义答案正确**，需下游事实核查。
- Workspace: 测试进程使用临时 SQLite 与禁用出站 socket；**这不是隔离不可信代码的内核级 Sandbox**，不能以此宣称已经会安全运行 Claude Code。
- Failure policy: 工具抛异常、返回非合成数据、无效 Schema、无限循环、超出预算都按明确 reason 拒绝；不会无上限重试或删除用户文件。
- Memory: Planner 只见窗口内有限的工具结果；压缩时淘汰旧观测、保留安全来源 ID；**不是**持久化跨线程记忆，也没有实现子 Agent 协作。
- Token: `OpenAIJsonPlanner` 只使用 provider 报告的 prompt/completion tokens；未报告时为 `null`，不把字符估计伪造成真实用量。当前离线数据全部 `NOT_MEASURED`。

## 2. 北极星指标与护栏

**离线工程指标**：合成任务中 `policy_contract_passed / cases`，其分子以预先声明的期望 status、reason、真实工具序列和最少证据数为准。

**这不是业务北极星**。真正的业务北极星应该是“有可靠来源且符合审查要求的有效咨询任务完成比例”，需要人工校验任务规格、授权真实数据与独立评测集后才能报告，目前 `NOT_MEASURED`。

护栏：拒绝未授权真实数据、无效参数、重复工具循环、伪造引用、错误状态被包装成成功。报告要同时显示安全阻断数和成功任务数，不把安全拒绝算成业务完成。

## 3. 30 条可复查合成用例

`benchmarks/fixtures/readonly_harness_cases.json` 为显式 JSON 预期规则（非 LLM 生成的答案）：
- 8 条单院校查证；
- 8 条单专业查证；
- 4 条院校+专业多步查询；
- 2 条 BM25 知识检索；
- 8 条故意触发的负控制：不存在工具、错误参数、伪造证据、重复调用、工具预算、Planner 异常、工具异常、非合成来源。

`ScriptedController` 依据固定脚本提供选择，保证 CI 不调用第三方模型并可稳定回归。**即便 30/30 通过，也只能证明这一组业务+安全契约在固定脚本下通过，不证明真实 LLM 自主规划、语义正确率或真实考生转化。**

运行命令：

```bash
python -m benchmarks.readonly_harness_eval --out /tmp/gaobao-readonly-harness.json
pytest -q tests/test_readonly_harness.py tests/test_readonly_harness_eval.py --override-ini addopts=''
```

数据生成与执行发生在隔离临时数据库，输出仅含 Case ID、状态、工具名、次数、耗时和原因码；不得向日志导出用户原始画像、模型回答、数据库密码或真实学校信息。

## 4. 能力合同与可验证边界

| 验收问题 | 对应代码 | 可由证据证明 | 尚不能证明 |
|---|---|---|---|
| “你的 Agent 怎么自主选工具？” | `OpenAIJsonPlanner.decide` + `run_readonly_agent` | 有模型可替换接口与 tool/finish/abstain 循环 | 未运行真实模型 benchmark |
| “工具步长怎么治理？” | `HarnessLimits` 与 `tool_sequence` | 有真实工具调用计数、步数与重复工具阻断 | 未证明真实 Agent 平均工具调用下降 |
| “遇到工具故障呢？” | `tool_execution_failed` 等 + safety fixtures | 失败不返回伪成功且可归因 | 不涵盖公网分布式恢复 |
| “Token 怎么优化？” | `OpenAIJsonPlanner` usage 与有界 history | provider usage 采集接口，缺失明确 unknown | 无上线 Token 成本对照 |
| “有 Eval 么？” | `readonly_harness_eval.py` 30 cases | 可复现离线 policy Eval | 无语义 Ground Truth / 实际模型 Eval |
| “Sandbox 呢？” | 临时 SQLite、工具白名单、网络 socket guard | CI 中限定工具能力与数据范围 | 不是 Docker/nsjail/firecracker OS 隔离 |

## 5. 后续阶段的不可跳过门禁

1. **真实模型实验**：在显式选择模型及合法费用预算的情况下，以同一固定 case 集测多次，保存参数、模型版本、run-id、provider usage 和逐项失败分类；模型不能使用 `ScriptedController` 结果冒名替代。
2. **答案真实性**：独立来源与语义核验，对“工具来源存在但结论错”设置负控制；加入拒答误判率与风险引用支持率。
3. **可靠性深度**：若进入长事务和有副作用工具，再引入 durable run/lease/幂等/HITL 与真正可恢复的工作流；不要为了展示框架滥加写操作。
4. **Sandbox 与 Coding Agent**：与招生查询是不同的任务边界。若需评估通用编码任务，应在独立的受限容器和无业务凭证的示例仓中验证；不在志愿查询服务内开放 shell。
5. **优化实验**：针对上下文摘要/工具描述/步数预算进行预注册候选改动，对照真实模型的独立 holdout；未获得显著且可重复的质量-成本收益前不宣传“提升 X%”。
6. **能力发布 Gate**：任何模型驱动优化声明都要求合并源码、精确 SHA 的 CI、可复现报告、真实模型评测和证据索引。当前合成 30-case 验收只支持描述独立只读 Harness 的合成策略合同。

## Decision-card malformed response accounting (2026-11-24)

As of 2026-11-24, a response with missing, malformed or non-object JSON, or a missing/non-string action discriminator, is blocked with `planner_response_invalid`; valid provider-reported usage is preserved. The independent redacted trace validator accepts that terminal failure, and the regression checks zero tool execution and no raw response persistence. Forbidden tools, repeated calls, wrong argument types, premature finish and unsafe abstention also return the same fixed invalid decision, retaining reported usage without tool execution or raw output. Transport failures or missing provider usage remain separately blocked. The tests exercise both malformed and forbidden responses through the synthetic SQL harness. No real provider calls or business success claims.

Validation: `pytest -q tests/test_decision_card_model_planner.py tests/test_decision_card_trace_eval.py` (CI required).
