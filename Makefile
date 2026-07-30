.DEFAULT_GOAL := help

UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
export UV_CACHE_DIR

.PHONY: help install install-production dev-backend dev-frontend test lint frontend-build verify format build infra-up opensearch-init infra-down compose-up compose-down logs

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Shopping Agent commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install backend and frontend development dependencies
	$(UV) sync --extra test
	cd frontend && $(NPM) install

install-production: ## Install backend dependencies with Redis/OpenSearch/Faiss adapters
	$(UV) sync --extra production

dev-backend: ## Run FastAPI on http://127.0.0.1:8000
	$(UV) run uvicorn app.api.server:app --reload --host 127.0.0.1 --port 8000

dev-frontend: ## Run Vite on http://127.0.0.1:5173
	cd frontend && $(NPM) run dev -- --host 127.0.0.1

test: ## Run backend and frontend tests
	$(UV) run pytest -q
	cd frontend && $(NPM) run test

lint: ## Check Python formatting and lint rules
	$(UV) run ruff check app tests
	$(UV) run ruff format --check app tests

frontend-build: ## Type-check and create the frontend production bundle
	cd frontend && $(NPM) run build

verify: lint test frontend-build ## Run the same quality gate as CI

format: ## Format Python source and tests
	$(UV) run ruff format app tests

build: ## Build both Docker images
	$(COMPOSE) --profile app build

infra-up: ## Start Redis and OpenSearch only
	$(COMPOSE) up -d redis opensearch

opensearch-init: ## Create the category index and hybrid search pipeline
	$(COMPOSE) --profile bootstrap run --rm opensearch-init

infra-down: ## Stop local middleware without deleting data volumes
	$(COMPOSE) down

compose-up: ## Build and start the full application at http://127.0.0.1:8080
	$(COMPOSE) --profile app up --build -d

compose-down: ## Stop the full application without deleting data volumes
	$(COMPOSE) --profile app down

logs: ## Follow backend and frontend container logs
	$(COMPOSE) --profile app logs --follow backend frontend
