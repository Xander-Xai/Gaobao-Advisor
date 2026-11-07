# 故障排查手册 (FAQ)

> **版本**: 3.0.0
> **最后更新**: 2026-10-31
> **适用场景**: 自托管（Docker）部署的故障排查与应急响应；配合仓库内实际存在的
> `scripts/setup_ssl.sh` / `scripts/backup_db.sh` / `docker-compose.prod.yml`。
> 默认演示不需要 LLM Key；`LLM_API_KEY` 只在启用模型生成时需要。
> 本项目默认合成数据、RAG 默认启用（随仓原创合成语料），不构成生产 SLA 或真实志愿决策依据。

---

## 📋 目录

1. [启动问题](#1-启动问题)
2. [数据库问题](#2-数据库问题)
3. [LLM API 问题](#3-llm-api-问题)
4. [性能问题](#4-性能问题)
5. [监控告警](#5-监控告警)
6. [前端问题](#6-前端问题)
7. [SSL/HTTPS 问题](#7-sslhttps-问题)
8. [备份恢复](#8-备份恢复)
9. [紧急回滚](#9-紧急回滚)

---

## 1. 启动问题

### Q1.1: Docker 容器启动失败

**症状**：
```bash
docker compose up -d
# ERROR: for api: Container exited with code 1
```

**诊断步骤**：

1. 查看日志：
```bash
docker logs gaokao-api-prod --tail 50
```

2. 检查常见错误：

**错误 A**: 缺少环境变量
```
ValueError: LLM_API_KEY is not set
```
**解决**：
```bash
cp .env.example .env.production
vim .env.production  # 填写 LLM_API_KEY
docker compose restart
```

**错误 B**: 端口被占用
```
OSError: [Errno 98] Address already in use
```
**解决**：
```bash
# 查找占用端口的进程
sudo lsof -i :8000
sudo kill <PID>

# 或修改端口
sed -i 's/8000:8000/8001:8000/' docker-compose.prod.yml
```

**错误 C**: 数据库文件权限问题
```
PermissionError: [Errno 13] Permission denied: '/app/data/gaokao.db'
```
**解决**：
```bash
sudo chown -R 1000:1000 data/
docker compose restart
```

---

### Q1.2: Nginx 启动失败

**症状**：
```bash
docker logs gaokao-nginx-prod
# nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)
```

**解决**：
```bash
# 停止占用 80/443 端口的服务
sudo systemctl stop apache2  # 如果有 Apache
sudo fuser -k 80/tcp
sudo fuser -k 443/tcp

# 重启 Nginx
docker compose restart nginx
```

---

### Q1.3: 健康检查失败

**症状**：
```bash
curl http://localhost:8000/api/v1/health
# curl: (7) Failed to connect to localhost port 8000
```

**诊断**：

1. 检查容器状态：
```bash
docker ps | grep gaokao
# 如果状态是 "Restarting"，查看日志
docker logs gaokao-api-prod --tail 100
```

2. 检查依赖服务：
```bash
# Redis（如果使用）
docker exec -it gaokao-redis redis-cli ping

# 数据库
docker exec -it gaokao-api-prod python -c "
from db.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('SELECT 1'))
    print('DB OK:', result.scalar())
"
```

---

## 2. 数据库问题

### Q2.1: 数据库连接超时

**症状**：
```
sqlalchemy.exc.OperationalError: database is locked
```

**原因**：SQLite WAL 模式未启用或并发过高。

**解决**：

1. 检查 WAL 模式：
```bash
docker exec -it gaokao-api-prod python -c "
from db.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('PRAGMA journal_mode'))
    print('Journal mode:', result.scalar())
"
# 预期输出: wal
```

2. 如果不是 WAL，手动启用：
```bash
docker exec -it gaokao-api-prod python -c "
from db.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    conn.execute(text('PRAGMA journal_mode=WAL'))
    conn.commit()
"
```

3. 优化并发设置：
```python
# 在 db/database.py 中调整
connect_args = {
    'timeout': 30,  # 增加到 30 秒
    'check_same_thread': False
}
```

---

### Q2.2: 数据库文件损坏

**症状**：
```
sqlite3.DatabaseError: database disk image is malformed
```

**解决**：

1. 从备份恢复：
```bash
# 停止服务
docker compose down

# 找到最新备份
ls -lt backups/ | head -5

# 恢复备份
gunzip backups/gaokao_db_YYYYMMDD_HHMMSS.sql.gz
cp backups/gaokao_db_YYYYMMDD_HHMMSS.sql data/gaokao.db

# 重启服务
docker compose up -d
```

2. 验证数据完整性：
```bash
docker exec -it gaokao-api-prod python scripts/validate_data.py
```

---

### Q2.3: 查询速度慢

**症状**：API 响应时间 > 5 秒

**诊断**：

1. 检查慢查询：
```bash
docker exec -it gaokao-api-prod python -c "
from db.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    # 启用查询分析
    conn.execute(text('PRAGMA analysis_limit=1000'))
    conn.execute(text('ANALYZE'))
"
```

2. 添加缺失索引：
```bash
docker exec -it gaokao-api-prod python -c "
from db.database import engine
from sqlalchemy import text
with engine.connect() as conn:
    # 检查索引
    conn.execute(text('''
        CREATE INDEX IF NOT EXISTS idx_scores_school_province 
        ON scores(school_name, province);
    '''))
    conn.commit()
"
```

---

## 3. LLM API 问题

### Q3.1: LLM API 调用失败

**症状**：
```
openai.APIError: Connection error
```

**诊断**：

1. 检查 API Key：
```bash
echo $LLM_API_KEY | wc -c
# 应该 > 20 字符
```

2. 测试连通性：
```bash
curl -X POST https://api.deepseek.com/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 10
  }'
```

3. 检查配额：
```bash
# DeepSeek Dashboard: https://platform.deepseek.com/usage
```

**解决**：

- API Key 过期：重新生成并更新 `.env.production`
- 配额耗尽：充值或切换到备用提供商
- 网络问题：检查防火墙/代理设置

---

### Q3.2: LLM 响应延迟高

**症状**：P95 延迟 > 5 秒

**优化方案**：

1. 启用 RAG 缓存：
```bash
# 检查 Redis 是否运行
docker exec -it gaokao-redis redis-cli ping

# 查看缓存命中率
docker exec -it gaokao-api-prod python -c "
from server.services.rag_cache import get_rag_cache
cache = get_rag_cache()
print(cache.get_stats())
"
```

2. 调整模型参数：
```yaml
# config/llm_providers.yaml
temperature: 0.7  # 降低到 0.5-0.7
max_tokens: 512   # 减少到 256-512
```

3. 使用更快的模型：
```bash
# 从 deepseek-chat 切换到 deepseek-coder（更快）
sed -i 's/deepseek-chat/deepseek-coder/' .env.production
docker compose restart
```

---

## 4. 性能问题

### Q4.1: 内存使用过高

**症状**：Grafana 显示内存 > 90%

**诊断**：

1. 检查内存泄漏：
```bash
docker stats gaokao-api-prod
# 观察 RES 内存是否持续增长
```

2. 查看 Python 对象：
```bash
docker exec -it gaokao-api-prod python -c "
import tracemalloc
tracemalloc.start()
# ... 执行一些操作 ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
"
```

**解决**：

1. 重启服务（临时）：
```bash
docker compose restart api
```

2. 限制内存：
```yaml
# docker-compose.prod.yml
deploy:
  resources:
    limits:
      memory: 1G  # 硬限制
```

3. 优化向量索引加载：
```python
# server/services/vector_index.py
# 使用 mmap 而非全部加载到内存
self._index = faiss.read_index(self.index_path, faiss.IO_FLAG_MMAP)
```

---

### Q4.2: CPU 使用率 100%

**症状**：服务器负载过高

**诊断**：

1. 查看热点函数：
```bash
docker exec -it gaokao-api-prod py-spy top -p 1
```

2. 检查并发请求数：
```bash
docker logs gaokao-api-prod | grep "POST /api/v1/chat" | wc -l
```

**解决**：

1. 限流：
```bash
# 已在 middleware/ratelimit.py 中实现
# 调整速率限制
RATE_LIMIT_PER_MINUTE = 30  # 降低到 20
```

2. 水平扩展：
```bash
# 启动多个实例
docker compose up -d --scale api=3
```

---

## 5. 监控告警

### Q5.1: Prometheus 无法抓取指标

**症状**：Prometheus Targets 页面显示 "DOWN"

**诊断**：

1. 检查 /metrics 端点：
```bash
curl http://localhost:8000/metrics
# 应该返回 Prometheus 格式的指标
```

2. 检查 Prometheus 配置：
```bash
docker exec -it gaokao-prometheus cat /etc/prometheus/prometheus.yml
```

**解决**：

1. 重启 Prometheus：
```bash
docker compose restart prometheus
```

2. 重载配置：
```bash
curl -X POST http://localhost:9090/-/reload
```

---

### Q5.2: Grafana 仪表板无数据

**症状**：图表显示 "No data"

**诊断**：

1. 检查数据源：
```bash
# Grafana UI → Configuration → Data Sources
# 点击 "Test" 按钮
```

2. 检查时间范围：
```bash
# 确保时间范围包含最近的数据
# 右上角选择 "Last 1 hour"
```

**解决**：

1. 重新导入仪表板：
```bash
# Grafana UI → Dashboards → Import
# 上传 monitoring/grafana/dashboards/api-performance.json
```

2. 修复数据源 UID：
```yaml
# monitoring/grafana/datasources/datasources.yml
datasources:
  - name: Prometheus
    uid: prometheus  # 确保 UID 一致
```

---

### Q5.3: 告警未触发

**症状**：服务宕机但未收到告警

**诊断**：

1. 检查 Alertmanager 状态：
```bash
curl http://localhost:9093/-/healthy
```

2. 查看告警规则：
```bash
curl http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[] | select(.state=="firing")'
```

3. 测试通知渠道：
```bash
# Slack
curl -X POST $SLACK_WEBHOOK_URL \
  -H "Content-Type: application/json" \
  -d '{"text": "Test alert"}'

# Email
echo "Test" | mail -s "Test" admin@example.com
```

**解决**：

1. 检查告警规则语法：
```bash
docker exec -it gaokao-prometheus promtool rules check /etc/prometheus/rules/alerts.yml
```

2. 重新加载规则：
```bash
curl -X POST http://localhost:9090/-/reload
```

---

## 6. 前端问题

### Q6.1: 白屏或加载失败

**症状**：访问网站显示空白页

**诊断**：

1. 检查浏览器控制台：
```javascript
// F12 → Console
// 查看 JavaScript 错误
```

2. 检查网络请求：
```javascript
// F12 → Network
// 查看是否有 404/500 错误
```

**解决**：

1. 清除缓存：
```bash
# 硬刷新: Ctrl+Shift+R (Windows/Linux) 或 Cmd+Shift+R (Mac)
```

2. 重建前端：
```bash
cd frontend
npm run build
docker compose restart nginx
```

---

### Q6.2: SSE 连接断开

**症状**：聊天过程中断

**诊断**：

1. 检查 Nginx 配置：
```nginx
# nginx.conf
proxy_buffering off;  # 必须关闭
proxy_cache off;      # 必须关闭
```

2. 检查超时设置：
```nginx
proxy_read_timeout 300s;  # 增加到 5 分钟
```

**解决**：

1. 更新 Nginx 配置后重启：
```bash
docker compose restart nginx
```

2. 前端重连逻辑：
```javascript
// frontend/src/utils/sse.js
const eventSource = new EventSource(url);
eventSource.onerror = () => {
  setTimeout(() => reconnect(), 3000);  // 3 秒后重连
};
```

---

## 7. SSL/HTTPS 问题

### Q7.1: 证书过期

**症状**：浏览器显示 "您的连接不是私密连接"

**诊断**：

1. 检查证书有效期：
```bash
echo | openssl s_client -connect gaokao.example.com:443 2>/dev/null | openssl x509 -noout -dates
```

2. 检查自动续期 cron：
```bash
crontab -l | grep certbot
```

**解决**：

1. 手动续期：
```bash
./scripts/setup_ssl.sh gaokao.example.com admin@example.com
```

2. 强制续期：
```bash
certbot renew --force-renewal
docker compose restart nginx
```

---

### Q7.2: HTTP 不重定向到 HTTPS

**症状**：访问 http:// 不跳转到 https://

**诊断**：

1. 检查 Nginx 配置：
```nginx
# nginx.conf 应该有 80 → 443 重定向
server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

**解决**：

```bash
docker compose restart nginx
```

---

## 8. 备份恢复

### Q8.1: 备份失败

**症状**：`backups/` 目录为空

**诊断**：

1. 检查备份脚本权限：
```bash
ls -lh scripts/backup_db.sh
# 应该是 -rwxr-xr-x
```

2. 手动执行备份：
```bash
./scripts/backup_db.sh
# 查看错误信息
```

**解决**：

1. 修复权限：
```bash
chmod +x scripts/backup_db.sh
```

2. 检查磁盘空间：
```bash
df -h backups/
# 确保有足够空间
```

---

### Q8.2: 恢复备份后数据丢失

**症状**：恢复备份后发现部分数据缺失

**原因**：备份时服务仍在写入。

**预防**：

1. 备份前停止服务：
```bash
docker compose down
./scripts/backup_db.sh
docker compose up -d
```

2. 或使用 SQLite 在线备份：
```bash
docker exec -it gaokao-api-prod python -c "
import shutil
shutil.copy2('/app/data/gaokao.db', '/app/backups/gaokao_backup.db')
"
```

---

## 9. 紧急回滚

### Q9.1: 新版本有严重 Bug

**症状**：上线后发现功能异常

**回滚步骤**（5 分钟内完成）：

```bash
# 1. 停止当前服务
docker compose -f docker-compose.prod.yml down

# 2. 回滚代码
git checkout HEAD~1

# 3. 恢复数据库备份
gunzip backups/gaokao_db_LATEST.sql.gz
cp backups/gaokao_db_LATEST.sql data/gaokao.db

# 4. 重新启动旧版本
docker compose -f docker-compose.prod.yml --profile nginx up -d --build

# 5. 验证健康
curl -f http://localhost:8000/api/v1/health

# 6. 通知用户
# （通过公告或邮件）
```

---

### Q9.2: 数据库损坏且无备份

**症状**：数据库文件损坏且备份也损坏

**应急方案**：

1. 从知识库重建：
```bash
docker exec -it gaokao-api-prod python scripts/import_baidu_gaokao.py --resume
```

2. 从爬虫重新抓取：
```bash
docker exec -it gaokao-api-prod python scrapers/baidu_gaokao.py
```

---

## 📞 联系支持

如果以上方法都无法解决问题，请联系：

- **技术支持邮箱**: support@gaokao-advisor.com
- **Slack 频道**: #gaokao-support
- **GitHub Issues**: https://github.com/your-org/gaobao-advisor/issues

**提供以下信息**：
1. 错误日志：`docker logs gaokao-api-prod --tail 100`
2. 系统信息：`uname -a`
3. Docker 版本：`docker --version`
4. 复现步骤

---

**最后更新**: 2026-06-16  
**维护者**: AI Assistant
