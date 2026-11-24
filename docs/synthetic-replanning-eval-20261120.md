# P30 — 高考志愿顾问 Planning / Replanning 实验验收（2026-11-20）

> **Draft / SYNTHETIC_DEMO ONLY**。从旧 Draft PR #136 提取独立实验代码，直接以当前 master 为基线，不合并 #133 的其他变更。未合并主分支，未接入公开 `/chat`，无真实考生、官方招生数据、专家标注或真实模型执行成绩。

## 真实实现，不是只靠 Prompt 让模型“重试”

- `server/agent/synthetic_replanning.py`：独立 `Plan → Execute → Observe → Replan` 有界执行器。初次规划以及工具异常后的重新规划均由同一可替换 `PlanPolicy` 作出；`OpenAIReplanningPolicy` 使用实际 OpenAI-compatible JSON 规划调用（**只有显式授权后才运行**）。
- **业务边界**：恰好五项已冻结的虚构专业/两校录取/两校计划 SQL 查询；参数由服务器拥有，模型只能选择剩余步骤的子集或顺序。严禁任意 SQL、Shell、外部抓取、写入、真实志愿排序。
- **异常处理**：临时工具异常可以重新规划剩余步骤并最多重试一次；缺记录、来源不完整、选科冲突不允许重新规划“修复事实”，直接安全终止。`max_plan_rounds=3`、`max_tool_attempts=7`、`max_transient_retries=1`；模型 usage 超过预算或缺失时报错。
- **独立证据校验**：成功后的结果仍交给原有 `compose_synthetic_decision_card` Evidence Gate；`benchmarks.synthetic_replanning_eval` 使用独立 SQL Oracle 逐项校验事实与拒答。模型不能自行判定通过。
- **Trace**：记录每次 plan round、步骤数量、工具尝试/错误、安全终止原因、脱敏步骤事件、provider-declared tokens。不给模型原始 SQL 记录或敏感密钥，不把 scripted 运行冒称真实 LLM。
- **未实现**：计划跨进程持久化、通用多阶段任务 DAG、生产级多 Agent 路由、真实长任务模型表现、真实用户有效志愿交付率。现有 TaskRun/Checkpoint 能力不能直接代替这里新执行器的 Planner 状态持久化。

## 离线可复现验收（不需要密钥）

```bash
python -m pytest tests/test_synthetic_replanning.py -q --no-cov -o addopts=''
python -m benchmarks.synthetic_replanning_eval --inject-transient --out /tmp/gaobao-replanning-scripted.json
```

固定 8 条 **合成** 业务任务，涵盖正确历史证据和预期安全拒答；实验性注入第一所学校录取工具的单次临时异常，检查是否改变顺序、继续执行、控制预算，最终由独立 SQL Oracle 评判。不将测试中的假模型适配器视为真实 LLM。

## 真实模型实验（须项目所有者显式授权调用、配置密钥并承担费用）

```bash
# 在私有开发机手动设置 OPENAI_API_KEY，不写入仓库、日志或提交
export APP_ENV=development
python -m benchmarks.synthetic_replanning_eval \
  --mode model --model YOUR_AUTHORIZED_OPENAI_COMPATIBLE_MODEL \
  --permit-external-model --inject-transient --max-cases 8 \
  --out /tmp/gaobao-replanning-model.json
```

没有 `--permit-external-model` + key + model 就拒绝执行真实模型；不会在 CI 中偷偷访问 provider。只输出 case ID、匿名化轨迹、Token、工具次数和独立评分。

**后续证据门禁**：固定同模型、同 Prompt、8-case 合成测试；比较无故障 / 故障注入前后，记录完整 trace、成功率、平均重规划次数、工具成本、P95 和失败分类。扩展更多手工验收的复杂任务与 Holdout，再评估真实效果。若没有实际 provider 回执，全部真实模型指标为 `NOT_MEASURED`。

## 技术面试陈述界限

可以说：“设计了独立受控 Plan/Execute/Observe/Replan 执行器，对虚构高校数据的五项 SQL 查询实现工具临时故障后的受限重新规划、独立证据核验和离线故障注入测试。”

不能说：“真实模型重规划成功率达到 X%”“已经对真实志愿填报上线”“Coding Agent 长任务生产自愈”“优化了 X% Token”，除非有合法且独立核验的真实执行证据。
