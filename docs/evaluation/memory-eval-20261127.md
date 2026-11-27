# 记忆专项评测报告（2026-11-27）

> 证据等级必须分开阅读：
> - **SYNTHETIC**：本报告全部数值来自冻结的合成用例集与确定性替身摘要器，**没有真实模型调用**，
>   没有真实学生数据，没有真实用户。它衡量的是**上下文组装策略**的行为，不是模型摘要质量。
> - **REAL_MODEL**：`NOT_MEASURED`。真实 provider A/B 尚未执行。
> - **MANUAL_REVIEW_PENDING**：人工复核工作表已生成但**尚未有人签署**，不得当作质量结论。
>
> 原始数据：`docs/evaluation/memory-eval-20261127.json`（由 `python3 -m benchmarks.memory_eval --scripted-smoke` 生成）。
> 用例集：`benchmarks/fixtures/memory_eval_cases.json`（契约 `SYNTHETIC_MEMORY_EVAL_V1`，12 例）。

## 1. 用例覆盖

| 场景 | 用例 ID | 关键判据 |
|---|---|---|
| 多轮条件理解 | `multi_turn_conditional` | 旧轮次中的"计算机/省内"约束在压缩后仍可检索 |
| 指代消解 | `coreference_resolution` | "它/这两所学校"指向的实体名必须在上下文中可定位 |
| 城市与专业偏好延续 | `city_major_preference_continuity` | 跨会话偏好（广东 + 计算机科学与技术）必须出现 |
| 用户修改偏好 | `preference_revision` | 记忆中不得再出现被修订前的旧偏好值 |
| 跨会话记忆 | `cross_session_memory` | 新会话中仍能取到显式保存的偏好 |
| 错误记忆纠正 | `wrong_memory_correction` | 记忆中只出现纠正后的值 |
| 用户撤销授权 | `consent_revocation` | 撤销后记忆层不得再注入任何偏好 |
| 用户间记忆泄漏 | `cross_user_leakage` | 他人偏好不得出现在本用户上下文 |
| 过期记忆 | `expired_memory` | 过期条目不得进入提示词 |
| 摘要事实丢失 | `summary_fact_loss` | 仅存在于旧轮次的约束（选科、偏好类型）在压缩后保留 |
| 记忆中的 Prompt Injection | `memory_prompt_injection` | 注入文本被中和，且提示词标注"不可信输入" |
| 记忆与真实 SQL 数据冲突 | `memory_vs_sql_conflict` | 当前 SQL 证据不被记忆覆盖；偏好仅作为不可信偏好出现 |

## 2. 四臂受控对比

四个臂调用**生产代码路径**（`build_chat_context` / `build_compacted_chat_context` /
`build_durable_chat_context` + `SessionCompactionStore`），同一用例、同一当前任务、
同一记忆块；每例跑两轮（冷/热）以区分"一次性成本"与"每轮成本"。

| 臂 | 关键事实保留率 | 越权泄漏 | SQL 证据丢失 | 上下文 Token 均值 | 摘要调用（第1轮/第2轮） | 组装延迟均值 |
|---|---|---|---|---|---|---|
| `no_summary`（全量原文） | 1.000 | 0 | 0 | 260.08 | 0 / 0 | 0.039 ms |
| `sliding_window`（近 2 轮） | **0.857** | 0 | 0 | **130.75** | 0 / 0 | 0.036 ms |
| `per_request_summary`（每轮摘要） | 1.000 | 0 | 0 | 208.00 | 12 / **12** | 0.246 ms |
| `incremental_snapshot`（增量快照） | 1.000 | 0 | 0 | 208.00 | 12 / **0** | 4.375 ms |

（延迟为本地单次运行的机器相关值，仅作量级参考；Token 为仓库估算器口径，非真实计费 token。）

### 2.1 可从数据直接得出的结论

1. **静默滑窗是唯一丢事实的臂**：保留率 0.857，丢失的是仅存在于旧轮次的约束
   （`summary_fact_loss` 的"物理""师范"）。它最省（Token 约为全量原文的 50%），
   但没有"上下文不完整"的显式提示时，模型会误以为上下文完整。
2. **每轮摘要与增量快照在本合成集上保留率相同（1.000）**，但成本结构不同：
   每轮摘要在**每一轮**都要再调一次模型（12 → 12），增量快照只在冷启动付一次（12 → 0）。
   在真实 provider 下这是**付费调用次数**的差异，本报告不给出金额结论。
3. **增量快照的额外延迟来自真实 DB 往返与 CAS**（均值 4.4 ms，最大 6.3 ms），
   属于存储开销，不是模型开销。
4. **四臂都没有出现越权泄漏或当前 SQL 证据丢失**：记忆层只以 user 角色、不可信标注注入，
   当前任务永不被截断。

### 2.2 不能从本报告得出的结论

- 不能得出"某个臂的真实回答质量更好"：本报告没有真实模型，没有真实评分。
- 不能得出成本/账单结论：替身摘要器不产生供应商调用。
- 不能得出阈值（`KEEP_RECENT_ROUNDS`、`SUMMARY_TRIGGER_TOKENS`）在真实高报流量下的最优值：
  本合成集使用 120 token 的触发阈值（生产默认 2000）以在短对话上触发压缩路径。

## 3. 摘要事实隔离探针

评测报告内含 `quarantine_probe`，直接展示隔离行为：

```
原始：1. 用户目标：找计算机专业。
      2. 明确约束：分数线620，位次12000。
      3. 未确认事项：（来源：kb_x § y）
隔离：1. 用户目标：找计算机专业。
      [已屏蔽：历史摘要中的招生数据未经核验，请以当前已核验数据为准]
      3. 未确认事项：
```

分数线与位次整行被屏蔽，伪造的 `（来源：…）` 引用被整体移除。

## 4. 人工复核

`manual_review` 工作表已随报告生成（`status = MANUAL_REVIEW_PENDING`），
逐例列出 `expected_facts` / `forbidden_facts` 与待填的 `reviewer_verdict` / `reviewer_note`。
**在人工签署之前，不得把本报告的保留率当作答案质量结论。**

## 5. 复现

```bash
# 离线合成评测（无网络、无模型调用）
python3 -m benchmarks.memory_eval --scripted-smoke --out /tmp/memory_eval.json

# 单元测试（含四臂对比的结构性断言）
python3 -m pytest tests/test_memory_eval_suite.py -q --no-cov
```

真实模型评测入口已预留但**未实现**：
`--execute` 需要同时给出 `--permit-external-model` 与 `--confirm-provider-charges`，
当前实现会明确拒绝并返回 `REAL_MODEL evidence is not implemented in this revision`，
以免误用合成数据冒充真实效果。

## 6. 后续解除 NOT_MEASURED 所需条件

1. 固定同一模型/版本、同一批真实授权用例，做四臂真实 provider A/B。
2. 记录供应商报告的 token 与实际账单（不只是本地估算器）。
3. 独立评审 + 人工复核签署工作表。
4. 结论必须区分"合成""真实模型""人工复核"三类证据，不得混写。
