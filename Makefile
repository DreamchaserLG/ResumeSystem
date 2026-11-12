.PHONY: help up down build migrate seed api worker-logs test lint frontend frontend-install

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

logs: ## 查看 API 与 worker 日志
	docker compose logs -f api worker
