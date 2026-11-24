.PHONY: help up down build migrate seed api worker-logs test lint frontend frontend-install showcase showcase-verify showcase-dump

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## 启动基础设施（PG + Redis）并构建 API/worker
	docker compose up -d --build

down: ## 停止全部服务
	docker compose down

destroy: ## 停止并删除数据卷（危险：清空数据库）
	docker compose down -v

migrate: ## 执行数据库迁移（在 api 容器内按序执行 migrations/sql）
	docker compose exec -T api python -m migrations.migrate

seed: migrate ## 通过迁移写入种子数据

api: ## 本地开发：直接运行 API（需先 up + 本地 venv）
	uvicorn app.main:app --reload --port 8001

worker: ## 本地开发：运行 Celery worker
	celery -A worker.celery_app worker -l info --concurrency 2

frontend-install: ## 安装前端依赖
	cd frontend && pnpm install

frontend: ## 本地开发：启动前端 Vite dev server（http://localhost:5173）
	cd frontend && pnpm dev

test: ## 运行后端测试（本地 venv，需先 up + migrate）
        pytest tests -q

guard-test: ## 校验器与会话记忆单测
        pytest tests/test_answer_guard.py tests/test_conv_memory.py -q

prompt-eval: ## 提示词质量回归（需 RECRUITING_LLM_KEY）
        python -m eval.prompt_ab

logs: ## 查看 API 与 worker 日志
	docker compose logs -f api worker

SHOWCASE_DB ?= resume_showcase

showcase: ## 载入演示数据集（403 份简历 + 已解析结果）并启动，数据落在 resume_showcase
	@test -f db/showcase.sql.gz || { echo "缺少演示数据包：请先把 resume/、showcase/storage/、db/showcase.sql.gz 放回（仓库不随附简历）"; exit 1; }
	docker compose build api
	docker compose up -d --wait pg redis
	cd frontend && npm ci --no-audit --no-fund && npm run build
	POSTGRES_DB=$(SHOWCASE_DB) STORAGE_DIR=./showcase/storage docker compose run --rm --no-deps api python scripts/showcase_dataset.py restore
	POSTGRES_DB=$(SHOWCASE_DB) STORAGE_DIR=./showcase/storage docker compose up -d

showcase-verify: ## 核对演示数据：部门、岗位、每岗份数与发布状态
	python scripts/showcase_dataset.py verify

showcase-dump: ## 从当前展示库重新导出 db/showcase.sql.gz
	python scripts/showcase_dataset.py dump

