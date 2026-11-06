# Java 微服务增量扩展

状态：功能分支中的首版实现；未进行 Docker 集成验收、真实数据验证或公网生产验收。默认 Python/SQLite 部署不变。

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

默认 docker compose up 不启用 Java。注意：基础 Compose 仍将 Python API 的 8000 端口发布给宿主机，演示模式不能将 Java Gateway 当作唯一安全入口；生产网络必须取消该端口的公网暴露，并在入口层完成 TLS/认证。停机保留数据卷：

~~~bash
docker compose --env-file .env.full -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.java.yml --profile full --profile java down
~~~

## 测试

~~~bash
mvn -f java-services/pom.xml -B -ntp verify
~~~

JUnit/H2（MySQL 模式）测试覆盖参数化 SQL、分页、cursor、来源标记；网关测试验证限流 Key 不受 X-Forwarded-For 欺骗。上述单测不能替代 MySQL/Redis/Docker 的真实联调。必须人工测试 Chat SSE 事件、鉴权透传、两个网关副本的共享限流、数据库和 Redis 故障与健康检查。

## 分布式机制及边界

Spring Cloud Gateway 使用 Docker DNS 定位服务。Redis 令牌桶以 gaobao:catalog:public 作为**所有匿名目录流量共享的全局 Key**：默认每秒恢复 20 Token，最多突发 40，耗尽返回 HTTP 429。此设计刻意不信任客户端可伪造的代理 IP 头；不是按用户的额度，会让不同用户互相影响。Redis 故障不能静默放行。实际副本均衡需要用压测证据验证；没有引入 Nacos/Eureka，不声称提供跨可用区高可用、分布式事务或生产 SLA。

Catalog 只执行 SELECT，Hikari 连接设为 readOnly。为兼容现有 full 环境，演示 Compose 仍使用原 MySQL 账号；这**不是**数据库级最小权限。生产部署需由管理员新建只拥有 schools、admission_scores、enrollment_plans 三张表 SELECT 权限的专用账号，并通过受保护环境变量注入，不得把数据库密码提交到 git。Redis/MySQL 不应映射到公网。生产还需 TLS、Redis 认证、审计、链路追踪和故障演练。

## 验收门槛

- Maven、Compose config、Java 服务 readiness 全通过
- 老 Python API 与 SSE 未回归
- 分页、SQL 注入、错误码和数据 provenance 已核对
- 两个网关实例共享 Redis 限流且确实返回 429
- Redis/MySQL 不可用时有明确失败/降级表现
- 数据来源、隐私、密钥、最小权限、负载测试和 TLS 验证完毕

现有队列恢复边界详见 docs/superpowers/adr/ADR-008-mysql-redis-worker.md 和 ADR-009-redis-delivery-selection.md。
