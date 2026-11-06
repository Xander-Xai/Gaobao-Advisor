# Java 微服务增量扩展

状态：功能分支中的演示级扩展。Java 单元测试、MySQL/Redis/Python 容器联调、两个网关共享限流、Redis/MySQL 故障恢复已在 GitHub Actions 实测通过；不等于真实数据验证、跨主机高可用或公网生产验收。默认 Python/SQLite 部署不变。

## 架构

- Vue 3 -> Nginx :80 -> Java Gateway :8080
- Java Gateway -> /api/v1/catalog/** -> Java Catalog :8081
- Java Gateway -> /api/** (含 Chat SSE) -> 原 Python FastAPI :8000
- Nginx /ws/** -> Python FastAPI（语音 WebSocket 不通过 Java）
- Java Gateway -> Redis 7（跨副本统一限流）
- Java Catalog -> 现有 MySQL 8.4（参数化 SELECT；只读接口）
- 现有 Python Worker + Redis List + 事务 Outbox 继续使用，不复制任务队列。

技术版本：Java 21，Spring Boot 4.0.9，Spring Cloud 2025.1.4，Gateway WebFlux，Spring JDBC，Hikari，Redis。

## 启动

需要 Docker Compose v2；首先运行以下命令生成本地密码：

~~~bash
python scripts/setup_full_env.py
docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml --profile full --profile java config
docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml --profile full --profile java up --build -d
~~~

验证：

~~~bash
curl -i 'http://localhost/api/v1/catalog/schools?limit=2'
curl -i 'http://localhost/api/v1/catalog/schools/1'
curl -i 'http://localhost/api/v1/data/overview'
curl -i 'http://localhost/api/v1/health'
~~~

Java 新接口返回 items、total、count、next_cursor、has_more；查询参数为 q、province、level、school_type、limit（1—200）与 cursor。院校条目保留 special_type、data_source_note、is_synthetic，合成数据不得用于真实志愿决策。原 /api/v1/data/*、/api/v1/chat、管理员鉴权等逻辑仍由 Python 提供。

默认 docker compose up 不启用 Java，仍可直接访问宿主机 8000/3080。仅在启用 Java overlay 时，配置通过 Compose `!reset []` 取消 Python API 8000 和静态前端 3080 的宿主机端口，外部 HTTP 入口只保留 Nginx 80。此处仍是无 TLS 演示；生产必须加入 HTTPS、访问控制和网络隔离。停机保留数据卷：

~~~bash
docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml --profile full --profile java down
~~~

## 测试

~~~bash
mvn -f java-services/pom.xml -B -ntp verify
~~~

JUnit/H2（MySQL 模式）测试覆盖参数化 SQL、分页、cursor、来源标记；网关测试验证限流 Key 不受 X-Forwarded-For 欺骗。上述单测不能替代 MySQL/Redis/Docker 的真实联调。必须人工测试 Chat SSE 事件、鉴权透传、两个网关副本的共享限流、数据库和 Redis 故障与健康检查。

## 分布式机制及边界

Spring Cloud Gateway 使用 Docker DNS 定位服务。自定义 `FailClosedCatalogRateLimiterGatewayFilterFactory` 不使用 Spring Cloud 默认 RedisRateLimiter 的故障放行路径，而是通过 Redis Lua（`TIME` + 原子读改写）执行令牌桶，Redis 故障/脚本失败/超时均返回 HTTP 503。Redis 令牌桶以 `gaobao:catalog:public:v2` 作为**所有匿名目录流量共享的全局 Key**：默认每秒恢复 20 Token，最多突发 40，耗尽返回 HTTP 429。此设计刻意不信任客户端可伪造的代理 IP 头；不是按用户的额度，会让不同用户互相影响。Redis 故障不能静默放行。实际副本均衡需要用压测证据验证；没有引入 Nacos/Eureka，不声称提供跨可用区高可用、分布式事务或生产 SLA。

Catalog 只执行 SELECT，Hikari 连接设为 readOnly。为兼容现有 full 环境，演示 Compose 仍使用原 MySQL 账号；这**不是**数据库级最小权限。生产部署需由管理员新建只拥有 schools、admission_scores、enrollment_plans 三张表 SELECT 权限的专用账号，并通过受保护环境变量注入，不得把数据库密码提交到 git。Redis/MySQL 不应映射到公网。生产还需 TLS、Redis 认证、审计、链路追踪和故障演练。

## 已取得的演示环境验收证据（2026-11-06）

- Java Maven 单元测试通过（移除旧 KeyResolver 后再次以当前分支 CI 为准）；Java + Python + MySQL + Redis 容器联调通过。
- 真实 SSE 会话 ID 和 HMAC Bearer 鉴权跨 Nginx -> Java -> Python 透传通过。
- 2 个网关实例共享 Redis 限流，20 次突发请求仅 4 次放行，两个实例均返回 429。
- Redis 断连时两个网关 readiness 和目录查询失败关闭，Redis 恢复后两个实例可再次查询。
- MySQL 断连时目录服务 readiness/查询失败，恢复后 Java 和 Python API 恢复。
- [完整故障测试与日志](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167546309)、[原项目回归 CI](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167551859)、[Java/Python/TS CodeQL](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/42167551947)。

详细范围及尚未达到的生产门槛见 [2026-11-06 验收报告](audits/2026-11-06-java-distributed-acceptance.md)。

## 生产验收门槛（尚未完成）

- Maven、Compose config、Java 服务 readiness 全通过
- 老 Python API 与 SSE 未回归
- 分页、SQL 注入、错误码和数据 provenance 已核对
- 两个网关实例共享 Redis 限流且确实返回 429
- Redis/MySQL 不可用时有明确失败/降级表现
- 数据来源、隐私、密钥、最小权限、负载测试和 TLS 验证完毕

现有队列恢复边界详见 docs/superpowers/adr/ADR-008-mysql-redis-worker.md 和 ADR-009-redis-delivery-selection.md。
