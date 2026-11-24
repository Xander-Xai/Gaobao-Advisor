# P32｜Context Engineering：原始 vs 证据压缩 A/B 评测

> 日期：2026-11-20。仅对 **SYNTHETIC_DEMO 虚构高校数据** 开放的可选实验；该 Draft 依赖 PR #136。原有 `master` 和公开 `/chat` 不改变。本 PR 不能证明真实考生填报正确性、官方招生信息准确性或线上 Token 降本。

## 1. 设计：只改变模型可见的历史观察

复用原有 `DecisionCardModelPlanner`、相同 `prompt_for_variant("baseline")`、温度 0、工具 Schema、最大 5 次只读工具调用、相同 8 条冻结业务案例、独立 SQL Oracle 及脱敏 Trace 验收。

| 控制变量 | Arm A：full | Arm B：evidence_compact |
|---|---|---|
| System Prompt / 模型 | 相同 | 相同 |
| 工具 | major/admission/plan SQL | 完全相同 |
| 执行器和业务证据门禁 | 相同 | 相同 |
| 历史观察 | 当前完整 JSON（仍受原 Harness 的历史预算约束） | 只保留来源 ID、记录完整性、学校/专业身份、选科约束、专业门类及必要有效性标志 |
| SQL 原始事实 | 仍由服务端保有 | 不改变，最终报告与 Oracle 从 SQL 重构 |
| 不允许 | 真实学校、写库、Shell、外部抓取、任意数据来源 | 同左 |

关键代码：
- `server/agent/context_ablation.py`：严格 JSON/工具契约；逐步校验 provenance（来源不能伪造/丢失）、记录数量、被压缩的选科规则；若出现未知字段或新工具，拒绝静默裁剪。所有观测字段的精简由固定 Python 规则执行，不使用另一个模型“凭空总结”。
- `benchmarks/context_ab_eval.py`：单独进程和临时 SQLite 数据库执行每组实验，冻结 8 例、4 条 holdout；交替 A/B 顺序，隔离前一组数据库状态。记录真实模型 usage（若 provider 报告）、逐例 SQL Oracle 结果、Trace SHA、工具步数、历史上下文字符量和耗时。
- `tests/test_context_ab_eval.py`：溯源/身份/选科约束保真、伪造来源拦截、同条件配对检查、数据库端到端 8 例模拟模型、自主调用权限门禁。

**Important：** 历史观察的 `history_sent_chars` 是 **字符级输入体积代理指标**，不是模型 Token。只用供应商报告的 `prompt_tokens` / `completion_tokens` 进行真实 Token 质量—成本判断。输出 Token 和失败调用的未知用量都不能通过估算伪装成账单事实。

## 2. 默认可复现（绝不访问模型 API）

```bash
python -m pytest tests/test_context_ab_eval.py -q --no-cov -o addopts=''
python -m benchmarks.context_ab_eval --model YOUR_MODEL_ID
python -m benchmarks.context_ab_eval --scripted-smoke --out-dir /tmp/context-ab-smoke
```

无网络、无密钥的 smoke 会实跑现有合成 SQL 工具和独立 Oracle。脚本模拟 `tool / finish / abstain`，用于验证两个上下文策略都能完成同一套 8 条业务契约，且仅输出脱敏计数与哈希。**固定脚本的通过率和虚构 `usage` 绝不等于真实模型能力或 Token 节省**；Summary 中真实 Provider Tokens 标成 `NOT_MEASURED`。

## 3. 实际模型 A/B（仅项目所有者自行授权）

```bash
# 需在已授权的私有开发机中设置 OPENAI_API_KEY，不提交密钥。
export APP_ENV=development
python -m benchmarks.context_ab_eval \
  --execute --permit-external-model --confirm-provider-charges \
  --model YOUR_PINNED_MODEL_VERSION --trials 2 \
  --out-dir /tmp/context-ab-provider
```

执行前先运行 dry-run，查看最多模型决策次数；费用因模型与服务商价格未知而不预估。每轮 8 条任务 × 2 arms，至少 2 轮，**调用顺序正反交替**。每个 arm 新建独立虚构数据库；模型 ID、固定 Prompt SHA、工具契约 SHA、测试集 SHA 相等才允许配对；Provider 返回的 `response.model` 必须两组都存在且一致，否则拒绝比较。不读取生产数据库，不向提供方上传真实考生数据。若部分 Provider usage 未返回，不推断用量。

## 4. 指标与验收

主要指标：holdout SQL Oracle 事实支持/正确拒答、退化案例 ID、Provider input+output Tokens、工具调用次数、端到端时间，以及 `observation_chars`。不能以“减少字符”直接宣称“省 Token”。

仅在 **至少两轮真实供应商调用结果**、全部冻结约束一致、每轮 holdout 无通过→失败/安全拒答退化、每轮实测 Token 减少时，生成 `REVIEW_REQUIRED`；仍需人工复核错误归因与外部账单，**永不自动上线**。

## 5. 已知边界

1. 8 条均是人工冻结的**虚构业务用例**，其中 holdout 仅 4 条；统计显著性 `NOT_ESTABLISHED`。不是专业专家审核 Ground Truth。
2. 该压缩只影响 **DecisionCardModelPlanner 的模型输入历史观察**，不改变 Checkpoint、跨任务 Memory、长对话概括或所有 LangGraph 节点。
3. 现在无用户提供的供应商密钥/费用许可，因此真实模型压缩前后**质量与 Token 成绩仍为 `NOT_MEASURED`**；GitHub CI 只能证明脚本、证据边界、合成回放和安全回归。
4. 压缩策略是有选择的信息投影，而非通用无损压缩；在新业务字段、不同模型或更复杂任务里可能降低质量。出现此类变化必须扩充可信事实清单和独立 Eval。
