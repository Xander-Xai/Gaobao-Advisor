# P16｜独立规则 Oracle 与可复核的合成业务结果

> 本 PR 的 Oracle 是**独立代码路径、独立数据库访问方式**，不是另一个 LLM 自动给答案打分；它依然只基于固定虚构 SQLite 数据，**不是**真实招生 Ground Truth 或人工专业复核。

## 与 P15 的关键区别

P15 在 Agent Worker 完成多候选任务后，调用 `verify_synthetic_portfolio` 再次执行**同一个**多候选分类函数，能发现数据库中途变化与卡片被篡改，但难以发现分类函数本身的系统性缺陷。

P16 新增 `server/agent/portfolio_sql_oracle.py`：

- 用 **sqlite3 的只读 `mode=ro` 连接**，直接查询真实合成数据库的 `dataset_snapshots / schools / majors / admission_scores / enrollment_plans`，与原来的 SQLAlchemy Tool Adapter 和 Portfolio Builder 不共用判定函数。
- 独立校验 **3–8 个批准的虚构候选的顺序与全集**、虚构数据来源、录取类型、最低分/位次、招生计划、选科约束、排除理由码、两类条目数量以及工具调用契约。
- 独立重建与当前 SQL 行绑定的 `synthetic:major/admission/plan` SHA256 来源指纹，防止执行结果悄悄替换学校、专业、分数或来源。
- 针对完整缺证据的候选只允许 `needs_review`，对于完全可核验的**历史资料比较**才允许 `historical_candidates_reviewed`，仍禁止 `application_ranking`、`admission_probability`、`applicant_rank`、`human_review_complete=true`。
- Worker 保留原有第一道复核，再增加独立只读 SQL Oracle；任一门禁不通过，一律不会持久化伪造的“成功研究卡片”。异常信息不会返回模型或 SQL 原文。

## 失败注入与回归

`tests/test_independent_portfolio_sql_oracle.py` 在隔离子进程中覆盖：有效 5 候选（4 条历史记录、1 条排除）、9 种字段/来源/顺序/权限边界伪造、历史类全部拒答、数据库结果变化，以及**故意让第一道回执验证器返回 True**时仍被第二条 Oracle 拒绝。

```bash
pytest -q tests/test_independent_portfolio_sql_oracle.py tests/test_portfolio_taskrun_eval.py
python -m benchmarks.portfolio_taskrun_eval --out /tmp/portfolio-taskrun-eval.json
```

CI 增加 `portfolio-taskrun-independent-oracle.json`，报告只含固定合成案例的 TaskRun `SUCCEEDED / NEEDS_REVIEW / CANCELLED` 结果，不记录任何真实招生成功率或外部模型 Token。

## 特别不能宣称

- 不是人工审核标注的真实志愿方案 GT；
- 不是招生省份、专业组、特殊类型、位次、体检要求与真实政策的准确性认证；
- 不是统计意义上有代表性的模型 Eval、真实成本优化或受用户确认的方案交付；
- 不是独立部署的生产审计系统；这个 Oracle 依然与本地进程共享相同的合成数据库和配置。

若要跨越这些边界，必须先取得合法真实数据快照、冻结业务规则、形成可追溯人工标注，并在用户允许的模型费用预算内进行真正的模型基线与 Holdout 复测。
