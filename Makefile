# ============================================================
# OmniBase Makefile - 新手友好的命令集
# ============================================================
#
# 用法：
#   make            显示所有命令
#   make up         启动所有服务
#   make logs       看日志
#   ...
#
# Windows 提示：
#   - 原生 cmd/PowerShell 不支持 make，请使用：
#     1. WSL（推荐）：在 WSL 终端运行 make
#     2. Git Bash：随 Git for Windows 安装
#     3. 或直接用 docker compose 命令（见下方注释）
#
# 不想用 make？直接用这些等价命令：
#   make up        →  docker compose up -d
#   make down      →  docker compose down
#   make logs      →  docker compose logs -f
#   make ps        →  docker compose ps
#   make migrate   →  docker compose exec backend alembic upgrade head
#   make test      →  docker compose exec backend pytest
#   make lint      →  docker compose exec backend ruff check . &&
#                     docker compose exec backend mypy src &&
#                     docker compose exec frontend pnpm lint
# ============================================================

.DEFAULT_GOAL := help

# 颜色（大部分终端支持；不支持时显示原始代码也无害）
CYAN   := \033[36m
GREEN  := \033[32m
YELLOW := \033[33m
RESET  := \033[0m

.PHONY: help up down restart logs logs-backend logs-frontend logs-db ps build rebuild start stop

# ------------------------------------------------------------
# 服务生命周期
# ------------------------------------------------------------

help: ## 显示所有可用命令
	@echo ""
	@echo "$(GREEN)OmniBase$(RESET) - 命令清单"
	@echo ""
	@echo "$(CYAN)服务管理$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -v "内部" | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-22s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(CYAN)等价的 docker compose 命令$(RESET)（不用 make 时）"
	@echo "  $(YELLOW)up$(RESET)        →  docker compose up -d"
	@echo "  $(YELLOW)down$(RESET)      →  docker compose down"
	@echo "  $(YELLOW)logs$(RESET)      →  docker compose logs -f"
	@echo "  $(YELLOW)ps$(RESET)      →  docker compose ps"
	@echo ""

up: ## 启动所有服务（后台，首次会构建镜像）
	@echo "$(GREEN)[启动]$(RESET) 构建 + 启动 5 个服务..."
	docker compose up -d --build
	@echo ""
	@echo "$(GREEN)[完成]$(RESET) 服务已启动。"
	@echo "  前端：         http://localhost:3000"
	@echo "  后端 API：     http://localhost:8000/docs"
	@echo "  MinIO 控制台： http://localhost:9001"
	@echo ""
	@echo "首次启动需要执行数据库迁移：$(YELLOW)make migrate$(RESET)"

down: ## 停止所有服务（保留数据）
	@echo "$(YELLOW)[停止]$(RESET) 正在停止服务..."
	docker compose down
	@echo "$(GREEN)[完成]$(RESET) 所有服务已停止。数据保留在 volume 中。"

stop: down ## 别名：停止所有服务

restart: ## 重启所有服务
	docker compose restart
	@echo "$(GREEN)[完成]$(RESET) 所有服务已重启。"

start: up ## 别名：启动所有服务

# ------------------------------------------------------------
# 日志与状态
# ------------------------------------------------------------

logs: ## 实时查看所有服务日志（Ctrl+C 退出）
	docker compose logs -f

logs-backend: ## 仅看后端日志
	docker compose logs -f backend

logs-frontend: ## 仅看前端日志
	docker compose logs -f frontend

logs-db: ## 仅看数据库日志
	docker compose logs -f postgres

ps: ## 查看服务状态（healthy / starting / exited）
	@docker compose ps

# ------------------------------------------------------------
# 构建与重建
# ------------------------------------------------------------

build: ## 构建镜像（不启动）
	docker compose build

rebuild: ## 强制重新构建镜像（无缓存，用于依赖变更）
	@echo "$(YELLOW)[构建]$(RESET) 强制无缓存重建..."
	docker compose build --no-cache
	@echo "$(GREEN)[完成]$(RESET) 镜像已重建。运行 $(YELLOW)make up$(RESET) 启动。"

# ------------------------------------------------------------
# 数据库迁移
# ------------------------------------------------------------

.PHONY: migrate migrate-new migrate-down reset-db

migrate: ## 执行数据库迁移（升级到最新版本）
	@echo "$(GREEN)[迁移]$(RESET) 执行 alembic upgrade head..."
	docker compose exec -T backend alembic upgrade head
	@echo "$(GREEN)[完成]$(RESET) 数据库已迁移到最新版本。"

migrate-new: ## 创建新迁移（用法：make migrate-new m="add users table"） [内部]
	@if [ -z "$(m)" ]; then echo "$(YELLOW)用法：$(RESET) make migrate-new m=\"迁移描述\""; exit 1; fi
	docker compose exec -T backend alembic revision --autogenerate -m "$(m)"
	@echo "$(GREEN)[完成]$(RESET) 新迁移文件已生成。检查后执行 $(YELLOW)make migrate$(RESET)。"

migrate-down: ## 危险！回滚一个迁移（需精确确认词） [内部]
	@if [ "$(CONFIRM)" != "DOWNGRADE_OMNIBASE_ONE_REVISION" ]; then \
		echo "$(YELLOW)拒绝执行。请显式传入 CONFIRM=DOWNGRADE_OMNIBASE_ONE_REVISION$(RESET)"; exit 1; \
	fi
	docker compose exec -T backend alembic downgrade -1
	@echo "$(YELLOW)[完成]$(RESET) 已回滚一个迁移版本。"

reset-db: ## 危险！删除本地开发数据并重建数据库 [内部]
	@if [ "$(ENV)" != "development" ] || [ "$(POSTGRES_DB)" != "omnibase" ] || [ "$(CONFIRM)" != "DELETE_LOCAL_OMNIBASE_DATA" ]; then \
		echo "$(YELLOW)拒绝执行。仅允许 ENV=development POSTGRES_DB=omnibase CONFIRM=DELETE_LOCAL_OMNIBASE_DATA$(RESET)"; exit 1; \
	fi
	@echo "$(YELLOW)[警告]$(RESET) 已确认删除本地开发数据。"
	docker compose down -v
	docker compose up -d postgres minio redis
	@echo "等待数据库启动..."
	docker compose up -d --wait postgres minio redis
	docker compose run --rm --no-deps backend alembic upgrade head
	@echo "$(GREEN)[完成]$(RESET) 数据库已重置。"

# ------------------------------------------------------------
# 开发工具
# ------------------------------------------------------------

.PHONY: backend-shell frontend-shell db-shell shell

backend-shell: ## 进入后端容器 shell（bash）
	docker compose exec backend bash

frontend-shell: ## 进入前端容器 shell（sh）
	docker compose exec frontend sh

db-shell: ## 进入 PostgreSQL 交互式终端（psql）
	docker compose exec postgres psql -U omnibase -d omnibase

shell: backend-shell ## 别名：后端 shell

# ------------------------------------------------------------
# 测试与质量
# ------------------------------------------------------------

.PHONY: test test-backend test-frontend test-destructive test-destructive-down lint lint-backend lint-frontend typecheck format format-check

test: test-backend test-frontend ## 运行所有测试

test-backend: ## 运行后端非集成测试（pytest + 覆盖率）
	docker compose exec -T backend pytest -m "not integration" --cov=omnibase --cov-report=term-missing

test-destructive: ## 在一次性隔离数据库中运行破坏性集成测试
	@case "$(TEST_COMPOSE_PROJECT)" in omnibase-ci-*|omnibase-p34-*|omnibase-test-*) ;; \
		*) echo "$(YELLOW)TEST_COMPOSE_PROJECT must use an isolated omnibase-ci-/omnibase-p34-/omnibase-test- prefix$(RESET)"; exit 1 ;; \
	esac
	@case "$(TEST_DATABASE_NAME)" in omnibase_test_*) ;; \
		*) echo "$(YELLOW)TEST_DATABASE_NAME must use the omnibase_test_ prefix$(RESET)"; exit 1 ;; \
	esac
	@case "$(TEST_DATABASE_ROLE)" in omnibase_test_*) ;; \
		*) echo "$(YELLOW)TEST_DATABASE_ROLE must use the omnibase_test_ prefix$(RESET)"; exit 1 ;; \
	esac
	@if [ -z "$(TEST_DATABASE_PORT)" ] || [ -z "$(TEST_DATABASE_OWNER_PASSWORD)" ] || [ -z "$(TEST_DATABASE_PASSWORD)" ]; then \
		echo "$(YELLOW)Explicit TEST_DATABASE_PORT and both disposable database passwords are required$(RESET)"; exit 1; \
	fi
	@set -eu; \
		compose="docker compose -p $(TEST_COMPOSE_PROJECT) -f docker-compose.destructive-tests.yml"; \
		trap '$$compose down -v --remove-orphans' EXIT INT TERM; \
		$$compose up -d --wait postgres-test; \
		export OMNIBASE_INTEGRATION_TESTS=1; \
		export TEST_DATABASE_URL="postgresql+psycopg://$${TEST_DATABASE_ROLE}:$${TEST_DATABASE_PASSWORD}@localhost:$${TEST_DATABASE_PORT}/$${TEST_DATABASE_NAME}"; \
		export DATABASE_URL="$$TEST_DATABASE_URL"; \
		export MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=test_access MINIO_SECRET_KEY=test_secret; \
		export REDIS_URL=redis://localhost:6379/15; \
		export JWT_SECRET=test_secret_at_least_32_characters_long_for_validation; \
		cd backend; \
		uv run python tests/destructive_preflight.py; \
		uv run alembic upgrade head; \
		uv run pytest -m integration \
			tests/integration/test_p34_3_controlled_data_foundation.py::test_0006_empty_downgrade_and_reupgrade_are_safe; \
		uv run pytest -m integration tests/integration \
			-k "not test_0006_empty_downgrade_and_reupgrade_are_safe"

test-destructive-down: ## 强制移除一次性破坏性测试数据库
	@case "$(TEST_COMPOSE_PROJECT)" in omnibase-ci-*|omnibase-p34-*|omnibase-test-*) ;; \
		*) echo "$(YELLOW)Refusing cleanup without an isolated TEST_COMPOSE_PROJECT$(RESET)"; exit 1 ;; \
	esac
	@if [ "$(CONFIRM)" != "REMOVE_DISPOSABLE_TEST_DATABASE" ]; then \
		echo "$(YELLOW)Refusing cleanup without CONFIRM=REMOVE_DISPOSABLE_TEST_DATABASE$(RESET)"; exit 1; \
	fi
	docker compose -p $(TEST_COMPOSE_PROJECT) -f docker-compose.destructive-tests.yml down -v --remove-orphans

test-frontend: ## 运行前端测试
	docker compose exec -T frontend pnpm test

lint: lint-backend lint-frontend ## 运行所有 lint 检查

lint-backend: ## 检查后端代码（ruff + mypy）
	docker compose exec -T backend ruff check .
	docker compose exec -T backend mypy src
	@echo "$(GREEN)[OK]$(RESET) 后端 lint 通过。"

lint-frontend: ## 检查前端代码（eslint）
	docker compose exec -T frontend pnpm lint
	@echo "$(GREEN)[OK]$(RESET) 前端 lint 通过。"

typecheck: ## TypeScript 类型检查
	docker compose exec -T frontend pnpm typecheck

format: ## 自动格式化所有代码
	docker compose exec -T backend ruff format .
	docker compose exec -T backend ruff check --fix .
	docker compose exec -T frontend pnpm format
	@echo "$(GREEN)[完成]$(RESET) 代码已格式化。"

format-check: ## 仅检查格式（不修改）
	docker compose exec -T backend ruff format --check .
	docker compose exec -T backend ruff check .
	docker compose exec -T frontend pnpm format:check

# ------------------------------------------------------------
# MinIO 工具
# ------------------------------------------------------------

.PHONY: minio-shell minio-ls

minio-shell: ## 进入 MinIO 客户端 shell（mc）
	docker run --rm -it --network omnibase_omnibase-net \
		-e MC_HOST_local="http://minio:9000/$${MINIO_ROOT_USER:-omnibase}:$${MINIO_ROOT_PASSWORD}" \
		minio/mc:RELEASE.2024-10-02T08-27-28Z sh

minio-ls: ## 列出 MinIO bucket 中的文件
	docker compose exec minio mc ls --recursive local/omnibase-files/ 2>/dev/null || \
		echo "请先启动 MinIO 服务：make up"

# ------------------------------------------------------------
# 清理
# ------------------------------------------------------------

.PHONY: clean clean-all

clean: ## 停止服务并删除容器（保留数据）
	docker compose down --remove-orphans
	@echo "$(GREEN)[完成]$(RESET) 容器已清理，数据保留。"

clean-all: ## 危险！删除本地开发容器、数据和镜像 [内部]
	@if [ "$(ENV)" != "development" ] || [ "$(POSTGRES_DB)" != "omnibase" ] || [ "$(CONFIRM)" != "DELETE_ALL_LOCAL_OMNIBASE_RESOURCES" ]; then \
		echo "$(YELLOW)拒绝执行。仅允许 ENV=development POSTGRES_DB=omnibase CONFIRM=DELETE_ALL_LOCAL_OMNIBASE_RESOURCES$(RESET)"; exit 1; \
	fi
	@echo "$(YELLOW)[警告]$(RESET) 已确认删除本地开发资源。"
	docker compose down -v --rmi local --remove-orphans
	@echo "$(GREEN)[完成]$(RESET) 所有本地 OmniBase 资源已清除。"
