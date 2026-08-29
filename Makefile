.DEFAULT_GOAL := help

UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
GPU_COMPOSE = $(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
export UV_CACHE_DIR

.PHONY: help install install-production install-index product-index ebay-browse-snapshot ebay-browse-catalog product-opensearch customs-lookup amazon-category-cards category-index embedding-server embedding-up dev-backend dev-frontend test lint frontend-build browser-install browser-acceptance verify format build infra-up opensearch-init infra-down compose-up compose-gpu-up compose-down logs
.PHONY: reranker-server reranker-up

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Shopping Agent commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install backend and frontend development dependencies
	$(UV) sync --extra test
	cd frontend && $(NPM) install

install-production: ## Install backend dependencies with Redis/OpenSearch/Faiss adapters
	$(UV) sync --extra production

install-index: ## Install the local SentenceTransformer and Faiss product-index builder
	$(UV) sync --extra test --extra index

product-index: ## Download Electronics metadata and build the local Faiss product index
	$(UV) run --extra index python scripts/build_product_faiss.py --embedding-mode hashing

ebay-browse-snapshot: ## Fetch a bounded eBay Browse snapshot using EBAY_SNAPSHOT_QUERY
	@test -n "$${EBAY_SNAPSHOT_QUERY:-}" || (echo "set EBAY_SNAPSHOT_QUERY before fetching" && exit 1)
	$(UV) run python scripts/fetch_ebay_browse_snapshot.py --query "$${EBAY_SNAPSHOT_QUERY}" --marketplace-id "$${EBAY_MARKETPLACE_ID:-EBAY_US}" --environment "$${EBAY_ENV:-production}"

ebay-browse-catalog: ## Refresh a multi-keyword eBay snapshot from EBAY_SNAPSHOT_QUERY_FILE
	@test -n "$${EBAY_SNAPSHOT_QUERY_FILE:-}" || (echo "set EBAY_SNAPSHOT_QUERY_FILE before fetching" && exit 1)
	$(UV) run python scripts/fetch_ebay_browse_snapshot.py --query-file "$${EBAY_SNAPSHOT_QUERY_FILE}" --append --marketplace-id "$${EBAY_MARKETPLACE_ID:-EBAY_US}" --environment "$${EBAY_ENV:-production}"

product-opensearch: ## Build and atomically publish the OpenSearch product index
	HF_HOME=$${HF_HOME:-$(CURDIR)/data/.hf-cache} $(UV) run --extra index python scripts/build_product_opensearch.py \
		--input "$${AMAZON_PRODUCT_SNAPSHOT:-data/product_index/electronics-hash/products.jsonl}" --source fixture \
		--input "$${EBAY_PRODUCT_SNAPSHOT:-data/ebay_snapshots/browse.jsonl}" --source live \
		--embedding-mode "$${PRODUCT_SEARCH_EMBEDDING_MODE:-sentence-transformer}" \
		--model "$${EMBEDDING_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}" \
		--dimension "$${PRODUCT_SEARCH_EMBEDDING_DIMENSION:-384}"

customs-lookup: ## Query China's official tariff/VAT service for HS_CODE
	@test -n "$${HS_CODE:-}" || (echo "set HS_CODE before querying customs" && exit 1)
	$(UV) run python scripts/query_china_customs.py --hs-code "$${HS_CODE}" --goods-name "$${GOODS_NAME:-}"

embedding-server: ## Run the local SentenceTransformer query encoder on port 8090
	HF_HOME=$${HF_HOME:-$(CURDIR)/data/.hf-cache} $(UV) run --extra index uvicorn app.embedding.server:app --host 127.0.0.1 --port 8090

reranker-server: ## Run the local BGE-Reranker-v2-m3 service on port 8091
	HF_HOME=$${HF_HOME:-$(CURDIR)/data/.hf-cache} $(UV) run --extra index uvicorn app.reranker.server:app --host 127.0.0.1 --port 8091

embedding-up: ## Build and start the containerized SentenceTransformer query encoder
	$(COMPOSE) --profile app up --build -d embedding

reranker-up: ## Build and start the opt-in CPU BGE reranker container
	$(COMPOSE) --profile reranker up --build -d reranker-cpu
amazon-category-cards: ## Derive historical CategoryCards from the local product projection
	HF_HOME=$${HF_HOME:-$(CURDIR)/data/.hf-cache} $(UV) run --extra index python scripts/build_amazon_category_cards.py \
		--embedding-mode "$${CATEGORY_EMBEDDING_MODE:-sentence-transformer}" \
		--model "$${EMBEDDING_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}" \
		--dimension "$${EMBEDDING_DIMENSION:-384}"

category-index: ## Bulk-index generated CategoryCards into local OpenSearch
	$(UV) run python scripts/build_category_kb.py --input data/category_cards/amazon_electronics.jsonl \
		--opensearch-url "$${OPENSEARCH_URL:-http://127.0.0.1:9200}"

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

browser-install: ## Install the pinned Chromium runtime used by browser acceptance
	cd frontend && $(NPM) exec -- playwright install chromium

browser-acceptance: ## Run the controlled-backend real-browser acceptance matrix
	cd frontend && $(NPM) run browser:acceptance

verify: lint test frontend-build browser-acceptance ## Run the same quality gate as CI

format: ## Format Python source and tests
	$(UV) run ruff format app tests

build: ## Build both Docker images
	$(COMPOSE) --profile app build

infra-up: ## Start PostgreSQL, Redis and OpenSearch only
	$(COMPOSE) up -d postgres redis opensearch

opensearch-init: ## Create OpenSearch category mapping and hybrid search pipelines
	$(COMPOSE) --profile bootstrap run --rm opensearch-init

infra-down: ## Stop local middleware without deleting data volumes
	$(COMPOSE) down

compose-up: ## Build and start the full application at http://127.0.0.1:8080
	$(COMPOSE) --profile app up --build -d

compose-gpu-up: ## Build and start the opt-in vLLM/embedding/reranker GPU profile
	$(GPU_COMPOSE) --profile gpu --profile app up --build -d

compose-down: ## Stop the full application without deleting data volumes
	$(COMPOSE) --profile app down

logs: ## Follow backend and frontend container logs
	$(COMPOSE) --profile app logs --follow backend frontend
