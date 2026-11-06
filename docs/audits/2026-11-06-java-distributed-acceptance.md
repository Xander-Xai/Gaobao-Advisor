# Java 微服务与分布式可靠性严格验收（2026-11-06）

## 审计范围与结论

- 对象：`Xander-Xai/gaobao-advisor`，PR [#89](https://github.com/Xander-Xai/gaobao-advisor/pull/89) 中的 `feat/java-distributed-services` 分支；**未合并 `master`**。
- 结论：**演示/Compose 环境功能与故障注入 PASS；公网生产 NO-GO**。
- 已核验对象：Java 21 + Spring Boot Gateway、只读 Catalog、跨实例 Redis Lua 限流、Nginx 路由、Python SSE 与鉴权兼容、MySQL/Redis 健康检查和恢复。
- 未核验：真实省份官方招生数据准确性、可用区/跨宿主机容灾、高并发容量与生产 SLA、端到端 TLS、真实用户隐私流程、渗透测试、专用 SQL 最小权限凭据。

## 真实测试证据

| Gate | 结果 | 证据 |
|---|---|---|
| Java Maven compile/JUnit | PASS | [Java Actions](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167546309) |
| Compose host port isolation | PASS | 同上：`Java overlay host port isolation: PASS` |
| Java Catalog + Python + MySQL + Redis | PASS | 同上：`public API smoke: PASS` |
| SSE server-issued session/HMAC ownership/Authorization | PASS | 同上：`SSE, server-issued session ownership and Authorization passthrough: PASS` |
| Two independent Gateway replica shared throttle | PASS | 同上：20 请求，仅 4 放行；两个实例均收到 429 |
| Redis outage fail-closed and restart recovery | PASS | 同上：`Redis outage` 与 `Redis restart` PASS |
| MySQL outage fail-closed and restart recovery | PASS | 同上：`MySQL outage` 与 `MySQL restart` PASS |
| Original Python 3.10/3.11, frontend, DB, security CI | PASS | [完整 CI](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167551859) |
| CodeQL Python, Java/Kotlin, JavaScript/TypeScript | PASS | [CodeQL](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167551947) |

复现：运行 `bash scripts/verify_java_compose_ci.sh`（依赖 Docker Compose、Python 环境及运行器资源；脚本使用独立 Compose project，退出时销毁测试数据卷）；独立 Java 单测 `mvn -B -ntp -f java-services/pom.xml verify`。

**检验口径**：以上 PASS 为测试环境中的合成数据、HTTP 行为和容器故障结果，不是高考推荐准确率、真实用户请求负载或高可用 SLA 的统计证据。

## 对抗审查发现并修复的缺陷

1. **入口绕过：** 原基础 Compose 暴露 Python `8000` / 静态前端 `3080`，启用 Java 时仍会绕过 Gateway。Java overlay 通过 `ports: !reset []` 取消它们的宿主机映射，并加入 `docker compose config` 审核。默认非 Java 演示路径不变。
2. **Redis 故障健康检查超时：** 初次故障注入中 Gateway readiness 超时，后加明确 Redis connect/command timeout（1s/2s）。
3. **Redis 故障下限流 fail-open：** 默认 Spring Cloud Gateway `RedisRateLimiter` 在测试中 Redis 不可用时仍放行目录查询（HTTP 200）。已替换为仅绑定目录路由的 `FailClosedCatalogRateLimiterGatewayFilterFactory`，Redis Lua 原子扣减，错误/无决策/超时统一 HTTP 503；超额返回 429。已在两个真实 Gateway 实例、Redis kill/restart 中验证。
4. **代码安全检查遗漏 Java：** CodeQL 新增 `java-kotlin` 并成功通过。
5. **测试路径遗漏：** 追加双副本 429、SSE/Bearer 安全、Redis/MySQL 故障恢复；CI 流水线阻断，不能靠说明文档自证。

## 生产 NO-GO 事项

- `SECURITY.md` 和 [Issue #58](https://github.com/Xander-Xai/gaobao-advisor/issues/58) 记录历史 Git 对象包含移除的数据库及用户消息，当前仓库文件干净不代表 Git 对象历史已清除；需单独处理授权、外泄影响评估和历史清理。
- Java Catalog 演示 Compose 使用原 MySQL 业务账号而非实际专用 SELECT-only 账号。Hikari readOnly 不是 SQL GRANT；生产需独立受限账户，并在密钥系统中轮换。
- 演示入口仅 HTTP；无生产 TLS、WAF、入侵监测、Redis ACL/TLS、运行期认证与长期审计验收。
- Redis 全局限流只是一条跨实例统一目录流量预算，**不是每用户限流或配额结算**；需要公平性设计、压测及容量配置。
- Nginx `upstream java-gateway` 的 Docker DNS 寻址与两个容器直接测试不是完整的负载均衡/跨主机 HA 验证；需要单点故障注入、真实入口层轮询/摘除与恢复耗时测量。
- 现有 RAG/LLM 与志愿推荐仅发布 `SYNTHETIC_DEMO`；没有真实授权招生数据，禁止用于真实志愿填报决策。
- Java 依赖供应链 SCA、SBOM、签名以及镜像持续扫描仍未纳入硬门禁；CodeQL 成功不等于没有已知依赖漏洞。
- 本次检查没有提出或测量可用性 SLA、并发吞吐上限、p95/p99 延迟与长期运行稳定性。
- 未引入 K8s、Nacos/Eureka、分布式事务、自动服务发现/治理；不能宣称具备这些能力。

## 变更治理

PR #89 维持 Draft，不合并 master；本报告描述的已通过测试及未满足生产门槛须在后续版本持续维护。对生产部署另建单独的验收计划，不用演示 PASS 替代安全、数据和运维批准。
