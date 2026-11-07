# 生产环境部署检查清单（已更新）

> ⚠️ **状态：历史验收清单（2026-06-16），非当前验证结果。**
> 当前项目定位为**开源工程样本**：默认合成数据、RAG 默认启用（随仓原创合成语料），
> **未做生产级容量、并发或 SLA 验证**。因此本文中「达到生产就绪标准 / 建议立即上线 /
> 可用性 99.5% / 用户量与延迟预估」等描述**不代表当前状态**，仅为该次记录的当时假设。
> 部署请以 `README.md` 与仓库内实际存在的 `docker-compose.yml` / `docker-compose.prod.yml` 为准。
> 已知过时/缺失引用：`data/vector_index/` 当前不存在；负载测试脚本现位于
> `benchmarks/load/locustfile.py`（经 `benchmarks/load/run_suite.py` 驱动）；
> 实际测试指标见 `docs/performance/java-python-load.md`。

> 本清单基于 2026-06-16 验收报告更新，包含所有 P1 问题的解决方案。

---

## ✅ 环境准备

### 服务器要求
- [ ] Ubuntu 22.04 LTS 或更高版本
- [ ] 最低配置：2 CPU / 4GB RAM / 50GB SSD
- [ ] 推荐配置：4 CPU / 8GB RAM / 100GB SSD
- [ ] 静态 IP 地址
- [ ] 域名解析（A 记录指向服务器 IP）

### 软件依赖
```bash
# 安装 Docker 和 Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 验证安装
docker --version
docker compose version
```

---

## 🔒 安全配置（P1-1 已解决）

### SSL/HTTPS 证书
```bash
# 1. 设置 Let's Encrypt 证书
chmod +x scripts/setup_ssl.sh
./scripts/setup_ssl.sh gaokao.example.com admin@example.com

# 2. 验证证书
curl -f https://gaokao.example.com/api/v1/health

# 3. 确认证书自动续期
crontab -l | grep certbot
```

**预期结果**：
- ✅ HTTPS 正常工作
- ✅ 证书有效期 90 天
- ✅ 自动续期 cron job 已配置

### 环境变量配置
```bash
cp .env.example .env.production
vim .env.production
```

`docker-compose.prod.yml` 通过 required Compose `env_file` 注入 `.env.production`；
应用**不会**自动读取容器内 `/app/.env`。文件缺失、生产环境缺 `SESSION_SECRET`、
启用管理员却缺 `ADMIN_TOKEN_SECRET` 时容器会 fail-closed。可用以下脚本用临时测试凭据
验证（不读取真实 `.env.production`）：

```bash
PROD_API_IMAGE=gb-advisor:ci bash scripts/verify_prod_compose_env.sh
```

**必须配置的变量**：
```env
# LLM API Key（至少配置一个）
LLM_API_KEY=sk-your-deepseek-key
LLM_PROVIDER=deepseek

# Session Secret（32字节随机字符串，生产必填）
SESSION_SECRET=$(openssl rand -hex 32)

# 管理后台（可选；启用时必须同时设置 token secret，生产禁止明文 ADMIN_PASSWORD）
ADMIN_USERS_JSON='[{"username":"operator","password_hash":"pbkdf2_sha256$...","role":"operator"}]'
ADMIN_TOKEN_SECRET=$(openssl rand -hex 32)

# 管理员分布式安全（生产启用管理员时必填）
REDIS_URL=redis://redis:6379/0
ADMIN_RATE_LIMIT_BACKEND=redis
# 仅填写真实反向代理的精确 CIDR；默认空表示忽略 X-Forwarded-For
# ADMIN_TRUSTED_PROXIES=10.0.0.10/32

# Sentry DSN（可选，用于错误追踪）
SENTRY_DSN=https://xxx@sentry.io/xxx

# Redis 同时用于 RAG 缓存、管理员限流与 Token 撤销状态校验
# REDIS_URL 已在上方配置

# Grafana Admin Password
GRAFANA_ADMIN_PASSWORD=your-secure-password

# Slack Webhook URL（告警通知）
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX

# SMTP Configuration（邮件告警）
SMTP_PASSWORD=your-smtp-password
```

### MySQL Schema 迁移

```bash
# 空库或已在 Alembic 管理下的库
python -m db.migrate upgrade

# 旧 create_all 库：先校验 18 张表 schema，再 stamp 0001；不会创建/删除表
python -m db.migrate adopt-existing
python -m db.migrate upgrade

# 迁移后 seed（不依赖 create_all）
python -m scripts.seed_demo_data --database-url "$DATABASE_URL" --no-create-schema
```

`20261031_0002` 的 downgrade 被明确拒绝；回滚使用经验证的数据库备份/恢复。
详见 `docs/database-schema-matrix.md`。

---

## 📦 代码部署

### 克隆代码
```bash
git clone https://github.com/your-org/gaobao-advisor.git
cd gaobao-advisor
```

### 构建并启动
```bash
# 启动 API + Nginx（基础服务）
docker compose -f docker-compose.prod.yml --profile nginx up -d --build

# 启动监控栈（可选，推荐）
./scripts/deploy_monitoring.sh
```

### 验证服务
```bash
# 1. 健康检查
curl -f http://localhost:8000/api/v1/health
# 预期: {"status":"ok","version":"3.0.0","database":"connected"}

# 2. HTTPS 检查
curl -f https://gaokao.example.com/api/v1/health

# 3. 数据验证
docker exec -it gaokao-api-prod python scripts/validate_data.py

# 4. 监控端点
curl http://localhost:9090/-/healthy  # Prometheus
curl http://localhost:3000/api/health  # Grafana
curl http://localhost:3100/ready       # Loki
```

---

## 🧪 测试验证（P1-2 已解决）

### 后端测试
```bash
# 运行完整测试套件
python3 -m pytest tests/ -v --tb=short

# 实际结果: 885 passed / 1 skipped, 覆盖率 77.33%
```

### 前端测试
```bash
cd frontend
npm test

# 实际结果: 50 passed (MessageBubble, ChatArea, MessageInput, AppSidebar, AppHeader, AppRightPanel)
```

### 端到端测试
```bash
# 测试聊天流程
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test_001",
    "scene": "gaokao",
    "message": "我是山东考生，考了 580 分，物理类"
  }'

# 预期: SSE 流式响应，返回志愿建议
```

---

## 📊 监控配置（P1-5 已解决）

### Prometheus 指标
访问 `http://localhost:9090` 验证以下指标：
- ✅ `http_requests_total` - HTTP 请求总数
- ✅ `http_request_duration_seconds` - 请求延迟分布
- ✅ `llm_api_calls_total` - LLM API 调用数
- ✅ `rag_cache_hits_total` - RAG 缓存命中数
- ✅ `db_connection_errors_total` - 数据库连接错误

### Grafana 仪表板
访问 `http://localhost:3000`（admin/密码）：
- ✅ API Performance 仪表板已加载
- ✅ 数据源（Prometheus, Loki）已配置
- ✅ 实时图表正常显示

### 告警规则
在 Prometheus Alerts 页面验证：
- ✅ HighErrorRate (> 5%)
- ✅ HighResponseLatency (P95 > 3s)
- ✅ LLMAPIFailureRate (> 10%)
- ✅ ServiceDown

### 日志聚合
在 Grafana Explore 页面：
- ✅ 选择 Loki 数据源
- ✅ 查询 `{job="gaokao-api"}` 查看应用日志
- ✅ 查询 `{job="nginx-access"}` 查看访问日志

---

## 💾 数据备份

### 自动备份
```bash
# 验证备份脚本
./scripts/backup_db.sh

# 检查备份文件
ls -lh backups/
# 预期: gaokao_db_YYYYMMDD_HHMMSS.sql.gz

# 验证 crontab
crontab -l | grep backup_db
# 预期: 0 3 * * * /app/scripts/backup_db.sh
```

### 手动恢复测试
```bash
# 1. 停止服务
docker compose down

# 2. 恢复备份
gunzip backups/gaokao_db_LATEST.sql.gz
cp backups/gaokao_db_LATEST.sql data/gaokao.db

# 3. 重启服务
docker compose up -d

# 4. 验证数据
curl -f http://localhost:8000/api/v1/health
```

---

## 🚀 性能优化（P1-3, P1-4 已解决）

### 向量索引持久化
```bash
# 首次启动时会自动构建并保存向量索引
docker logs gaokao-api-prod | grep "Saved.*embeddings"

# 验证索引文件
ls -lh data/vector_index/
# 预期: faiss.index, metadata.json

# 重启后验证快速加载
docker restart gaokao-api-prod
docker logs gaokao-api-prod | grep "Loaded.*embeddings"
```

### RAG 缓存验证
```bash
# 检查 Redis 连接
docker exec -it gaokao-redis redis-cli ping
# 预期: PONG

# 查看缓存统计
docker exec -it gaokao-api-prod python -c "
from server.services.rag_cache import get_rag_cache
cache = get_rag_cache()
print(cache.get_stats())
"
# 预期: {'memory_entries': 0, 'redis_entries': 0, 'redis_connected': True}

# 执行几次查询后再次检查
# 预期: redis_entries > 0
```

### 性能基准测试
```bash
# 使用 locust 进行压力测试
pip install locust
locust -f benchmarks/load/locustfile.py --users 100 --spawn-rate 10 --run-time 60s

# 预期结果:
# - Requests/sec: > 40
# - Median response time: < 1.5s
# - 95th percentile: < 2.5s
# - Failure rate: < 1%
```

---

## 📈 上线后监控

### 关键指标（KPI）
| 指标 | 阈值 | 告警方式 |
|------|------|---------|
| 错误率 | > 5% | Slack + Email |
| P95 延迟 | > 3s | Slack |
| LLM API 失败率 | > 10% | Slack |
| 数据库连接失败 | > 0 | Slack + Phone |
| 磁盘使用率 | > 80% | Email |
| 内存使用率 | > 90% | Slack |

### 每日巡检
```bash
# 1. 检查服务状态
docker compose ps

# 2. 查看错误日志
docker logs --tail 100 gaokao-api-prod | grep ERROR

# 3. 检查磁盘空间
df -h

# 4. 检查备份
ls -lt backups/ | head -5

# 5. 查看 Grafana 仪表板
# 访问 http://localhost:3000 确认无红色告警
```

### 每周任务
- [ ] 审查告警历史，优化告警规则
- [ ] 检查日志增长趋势，调整保留策略
- [ ] 验证备份完整性（随机抽取恢复测试）
- [ ] 更新依赖包安全补丁

### 每月任务
- [ ] 轮换 SESSION_SECRET
- [ ] 审查用户反馈，优化回答质量
- [ ] 更新知识库（新增院校/专业数据）
- [ ] 性能调优（慢查询分析、缓存命中率优化）

---

## 🔄 回滚方案

### 快速回滚（5 分钟内）
```bash
# 1. 停止当前服务
docker compose -f docker-compose.prod.yml down

# 2. 回滚代码
git checkout HEAD~1

# 3. 恢复数据库备份
gunzip backups/gaokao_db_LATEST.sql.gz
cp backups/gaokao_db_LATEST.sql data/gaokao.db

# 4. 重新启动
docker compose -f docker-compose.prod.yml --profile nginx up -d --build

# 5. 验证健康
curl -f http://localhost:8000/api/v1/health
```

---

## ✅ 最终检查清单

### 安全性
- [x] HTTPS 证书配置完成（Let's Encrypt）
- [x] HSTS 安全头启用
- [x] CSP 内容安全策略配置
- [x] 速率限制器工作正常
- [x] 注入检测生效
- [x] XSS 防护双层验证

### 功能性
- [x] 聊天接口 SSE 流式响应正常
- [x] WebSocket 语音通话可用
- [x] RAG 检索准确率 > 80%
- [x] 会话持久化工作正常
- [x] 槽位提取准确

### 性能
- [x] P95 延迟 < 2s
- [x] 向量索引持久化（重启无需重算）
- [x] RAG 缓存命中率 > 30%
- [x] N+1 查询已消除
- [x] WAL 模式启用

### 监控
- [x] Prometheus 指标暴露
- [x] Grafana 仪表板配置
- [x] Loki 日志聚合
- [x] Alertmanager 告警规则
- [x] Slack/Email 通知配置

### 测试
- [ ] 后端测试（当前 885 passed / 1 skipped）
- [ ] 前端测试（当前 50 passed）
- [ ] 覆盖率（当前 77.33%，门禁 70%）
- [ ] E2E 测试（需自行搭建）

### 文档
- [x] SPEC.md 需求规格
- [x] CONVENTIONS.md 工程约束
- [x] README.md 用户手册
- [x] 部署指南完整
- [x] 故障排查 FAQ

---

## 🎯 上线决策

> 本节记录的是 2026-06-16 当时的内部判断，**不构成当前结论**。

当时的记录为「P1 问题已解决」。但以当前定位看，本项目是**开源工程样本**，默认合成
数据、RAG 默认启用（随仓原创合成语料），**未完成生产级验证**，因此**不应据此直接上线
真实志愿决策服务**。下方「预计效果」（用户量 / 延迟 / 可用性）为当时假设，非实测。

---

**验收人签字**: _______________  
**日期**: 2026-06-16
