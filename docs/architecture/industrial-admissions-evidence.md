# 工业招生证据治理（2026-11-25 候选）

基线 master `2c27d0208095ae8616b6a1e18fc93d47282169ba`，#198 已合并。基线 CI/CodeQL 成功，核查时 Java Services 仍在执行。#197 Context A/B 未合并，#199 Redis 公共 HTTP 限流 Draft 的 Python 测试失败。本文为候选增量，不是生产验收。

## 唯一主架构
Vue 3 → Nginx/Java Gateway → FastAPI / Java Catalog（SELECT-only）→ MySQL。FastAPI 唯一公开咨询入口为 LangGraph 12 节点准备图 + SSE 最终持久化/发布；Redis 与独立 Worker 执行质检。工业咨询直接构造历史 SQL 事实的文本和卡片，不执行公开模型生成或 Judge。开发合成模式保留模型及兼容路径。复杂研究由独立 operator API、异人审批、私有 journal、Deep Agents worker、只读 SQL、受限招生分析沙箱、独立 Oracle、私有报告承担；不构建第二套生产 Harness。

## 本次候选修复
| 问题 | 处理 |
|---|---|
| legacy 一分一段未按科类过滤 | 明确科类别名组，命中必须唯一，院校行与实际位次科类一致 |
| 分数误解析（截断数字、位次、分差、冲突字段） | 工业专用 exact_score，只接受明确整数分数或规范分数/位次组合；歧义拒绝，保留旧开发解析器 |
| 跨年、邻近分数、反推位次兜底 | 指定历史年度、省份和精确分数；无唯一依据拒答 |
| 专业反查可能使用其他专业录取线 | 仅展示院校级历史记录，保留批次，不宣称专业资格 |
| 卡片、生成文字与来源独立，画像完整度误作置信度 | 同一 SQL 行构造文字和卡片，内部表/行 ID；confidence=0（未测） |
| WS 参考图绕过 SSE，ASR/TTS 未实现 | 工业关闭 WS，开发参考保留 |
| 历史自由文本报告无当前证据版本 | 工业报告生成/读取/导出关闭，待证据绑定合同 |
| 会话读取失败静默回退 | SSE SESSION_UNAVAILABLE，不发布 done |
| 前置启发式与分析记录个人文本 | 工业路径跳过，保留独立数据质检 Worker |

GAOBAO_ADMISSIONS_YEAR 必填。±5% 位次邻域仅为历史数据展示，最多 10 行，按距离与主键排序，不是预测。完整选科组合不能安全映射时要求明确招生科类。内部数据授权标识和数据库行 ID 不等于独立许可证、官方来源 URL、不可变数据摘要或法律审批。运营者须独立审核授权、数据版本、来源与科类。

## 验收
`pytest -q tests/test_industrial_admissions_history.py tests/test_industrial_runtime.py tests/test_industrial_deployment_contract.py --no-cov -o addopts=''`

新增测试使用 SQLite 合成夹具验证科类/年份隔离、歧义拒答、同源卡片、缺政策依据拒答，以及明确分数/位次组合、冲突、分差、越界、非整数和布尔值的解析回归。真实 MySQL、真实授权数据、API/SSE 全链路、生产容器、负载及安全验收未执行。仍缺：#199 全局限流、同会话并发/幂等/删除竞态、数据快照与行级来源绑定、当年计划与资格规则、证据报告/语音安全交付、真实模型和 Context A/B、真实部署与运维。保留 NO-GO。私有研究 journal 不等于分布式图 checkpoint 或已测真实业务效果。近期公开增量不追溯为历史任职交付。
