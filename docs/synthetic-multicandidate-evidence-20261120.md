# P11｜高考顾问多候选合成约束筛选（不涉及真实志愿建议）

> 2026-11-20。仅用于 `SYNTHETIC_DEMO` 数据和工程验收。此功能是单独的离线决策辅助模块，**尚未接入原 /chat、Vue UI 或 TaskRun 的真实付费模型分支**；没有真实考生授权数据，不能称为可交付志愿方案。

## 解决的问题

既有证据卡固定比较 **两个虚构院校的同一虚构专业**，不足以演示多学校、多专业、多约束情况下怎样筛选、拒绝与回溯证据。

新建 `server/agent/synthetic_portfolio.py`：

- 冻结申请画像：省份、分数、历史选科类别、年份及 3～8 个**用户明确给定**的学校/专业组合；拒绝真实学校、省份、重复项和非法字段。
- 复用 `_major`、`_fetch_row` **实际隔离 SQLite** 查询，不联网，不提供写入能力、Shell、任意 SQL 或 LLM；每个不同专业只查询一次，所有组合都执行录取+招生计划查询。
- 严格检查录取记录的选科类别、最低分/位次有效性、招生计划人数、计划选科要求以及 3 条来源指纹。
- 只将符合这些**历史记录可核验条件**的条目列入 `accepted_historical_comparisons`，拒绝条目附固定理由码；**不凭历史分差计算录取概率、不做冲稳保评级、不产生志愿顺序**。其他当年规定、专业组、考生真实位次、政策、体检限制等不在当前模型中。
- `verify_synthetic_portfolio` 使用第二次 SQL 读取并校验完整输出，阻止成绩或来源字段被篡改。它是数据库再次核验，**不是独立实现的业务 oracle 或人工审核 Ground Truth**。

## 离线合成案例

固定 5 个院校-专业组合，其中四组有完整历史 SQL 来源，一组因数据缺失被排除。主要检验“历史比较”和“正确排除”必须分别统计：

| 指标 | 案例预期，不是生产指标 |
|---|---|
| 提供的虚构候选 | 5 |
| 能核验的历史候选 | 4 |
| 缺证据而排除 | 1 |
| 数据库工具调用 | 13（3 个不同专业 + 5×2 条录取/计划） |
| 成功生成真实志愿方案 | **0 / NOT_MEASURED** |
| LLM Token/美元成本 | 未调用 / null |

还包含所有候选选科不匹配时的全拒绝案例、虚假成绩/来源/排除列表的反例回归。

## 验证

```bash
python -m benchmarks.synthetic_portfolio_eval --out /tmp/portfolio-eval.json
pytest -q tests/test_synthetic_portfolio.py
```

新增 CI Artifact：`agent-harness-synthetic-evidence/multicandidate-synthetic-evidence.json`。

## 尚未实现的下一步

1. 将此模块纳入经审批的 TaskRun 及 Admin UI，增加候选池查询、用户偏好、科目/专业组等实际**来自权威合法数据**的完整条件；
2. 人工核对具体政策与招生计划快照，不把演示里的选科规则转化为真实学校资格建议；
3. 真实模型独立任务执行，固定 Prompt、模型、数据集与重复 Holdout A/B；
4. 验证强制终止模型和 Worker 的能力，而不仅是 PR #114 的 journal 取消和 stale-write fence。
