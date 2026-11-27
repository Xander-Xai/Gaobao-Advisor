PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else command -v python3 || command -v python; fi)

.PHONY: install install-dev install-legacy-admin test test-cov test-admin test-integration benchmark-mysql benchmark-worker \
        lint lint-fix format typecheck smoke-admin verify-prod-compose run run-api run-worker admin-env audit-deps lock clean help

help:  ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## 安装生产依赖
	pip install .

install-dev:  ## 安装开发依赖（含 pytest, ruff）
	pip install -e ".[dev]"

install-legacy-admin:  ## 安装可选的 legacy Streamlit 运营看板依赖（非运行时依赖）
	pip install -r requirements-legacy-admin.txt

test:  ## 运行测试
	$(PYTHON) -m pytest tests/

test-cov:  ## 运行测试（含覆盖率）
	$(PYTHON) -m pytest tests/ --cov=. --cov-report=term-missing --cov-report=html:htmlcov

test-admin:  ## 运行运营后台测试
	$(PYTHON) -m pytest tests/test_admin_*.py -q --no-header

benchmark-mysql:  ## 在专属 MySQL benchmark 库运行管理查询基准
	$(PYTHON) -m benchmarks.mysql_admin_benchmark --mysql-url "$${MYSQL_BENCH_URL:?set MYSQL_BENCH_URL}" --json-out docs/performance/results/medium-admin.json

benchmark-worker:  ## 运行 1/2/4 Worker 吞吐基准
	$(PYTHON) -m benchmarks.worker_throughput --mysql-url "$${MYSQL_BENCH_URL:?set MYSQL_BENCH_URL}" --redis-url "$${REDIS_BENCH_URL:?set REDIS_BENCH_URL}" --workers 1 2 4 --json-out docs/performance/results/worker-1-2-4.json

test-integration:  ## 用一次性 MySQL 8.4 + Redis 7 容器运行真实集成测试
	@set -eu; \
	docker rm -f gaobao-mysql-test gaobao-redis-test >/dev/null 2>&1 || true; \
	trap 'docker rm -f gaobao-mysql-test gaobao-redis-test >/dev/null 2>&1 || true' EXIT; \
	docker run --rm -d --name gaobao-mysql-test \
		-e MYSQL_ROOT_PASSWORD=testroot -e MYSQL_DATABASE=gaobao_test \
		-p 3307:3306 mysql:8.4 >/dev/null; \
	docker run --rm -d --name gaobao-redis-test -p 6380:6379 redis:7-alpine >/dev/null; \
	for i in $$(seq 1 60); do \
		if docker exec gaobao-mysql-test mysqladmin ping -h127.0.0.1 -uroot -ptestroot --silent >/dev/null 2>&1; then break; fi; \
		sleep 2; \
	done; \
	MYSQL_TEST_URL='mysql+pymysql://root:testroot@127.0.0.1:3307/gaobao_test?charset=utf8mb4' \
	REDIS_TEST_URL='redis://127.0.0.1:6380/0' \
	$(PYTHON) -m pytest tests/test_admin_mysql.py tests/test_mysql_migrations.py tests/test_admin_limits_redis.py tests/test_redis_worker.py tests/test_worker_integration.py tests/test_queue_recovery.py tests/test_llm_governor_redis.py -q --no-header --no-cov

typecheck:  ## 前端 TypeScript 类型检查
	cd frontend && npm run typecheck

admin-env:  ## 生成本地管理员凭据（.env.admin.local，已 gitignore）
	$(PYTHON) scripts/setup_admin_env.py

smoke-admin:  ## 一键验证后台登录/查询/质检/审核/审计（临时 SQLite）
	$(PYTHON) scripts/admin_smoke.py

verify-prod-compose:  ## 用临时测试凭据验证生产 Compose 的 fail-closed 与正常启动
	bash scripts/verify_prod_compose_env.sh

lint:  ## 检查代码质量
	ruff check .

lint-fix:  ## 自动修复 lint 问题
	ruff check --fix .

format:  ## 格式化代码
	ruff format .

run:  ## 启动 legacy Streamlit 运营看板（需先 make install-legacy-admin）
	@command -v streamlit >/dev/null 2>&1 || { \
		echo "streamlit 未安装。先运行: make install-legacy-admin"; exit 1; }
	streamlit run legacy/admin.py

run-api:  ## 启动 FastAPI 服务
	uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

run-worker:  ## 启动数据质检 Worker（需要 REDIS_URL）
	$(PYTHON) -m server.admin.worker

audit-deps:  ## 审计运行时依赖（必须零已知漏洞）
	pip install pip-audit
	pip-audit --requirement requirements.lock --progress-spinner off

lock:  ## 重新生成 requirements.lock（受 requirements.lock.constraints 约束）
	uv pip compile requirements.txt -o requirements.lock \
		--constraint requirements.lock.constraints

clean:  ## 清理构建产物
	rm -rf build/ dist/ *.egg-info htmlcov/ .pytest_cache/ __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
