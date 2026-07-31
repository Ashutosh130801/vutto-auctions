# Vutto Auctions — common tasks.  `make help` lists everything.
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## ---------------------------------------------------------------- docker
.PHONY: up
up: ## Build and start the whole stack (migrates + seeds automatically)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  App        http://localhost:8080"
	@echo "  API docs   http://localhost:8000/docs"
	@echo "  Metrics    http://localhost:8000/metrics"
	@echo "  Prometheus http://localhost:9090"
	@echo "  Grafana    http://localhost:3001  (admin/admin)"
	@echo ""
	@echo "  Sign in as admin@vutto.test / Admin@12345"
	@echo "         or  aarav@vutto.test / Demo@12345"

.PHONY: down
down: ## Stop the stack (keeps data)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete all data
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail application logs
	$(COMPOSE) logs -f api worker

.PHONY: scale
scale: ## Run 3 API replicas to demonstrate horizontal scaling
	$(COMPOSE) up -d --scale api=3 --no-recreate

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

## ---------------------------------------------------------------- backend
.PHONY: install
install: ## Install backend dev dependencies
	cd backend && pip install -r requirements-dev.txt

.PHONY: migrate
migrate: ## Apply database migrations
	cd backend && python -m app.cli migrate

.PHONY: revision
revision: ## Autogenerate a migration:  make revision m="add x"
	cd backend && alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Load demo data
	cd backend && python -m app.cli seed

.PHONY: config
config: ## Print the resolved configuration (credentials redacted)
	cd backend && python -m app.cli check

.PHONY: dev
dev: ## Run the API with hot reload
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: worker
worker: ## Run the lifecycle worker
	cd backend && python -m app.workers

.PHONY: test
test: ## Run the full test suite
	cd backend && pytest

.PHONY: test-fast
test-fast: ## Run tests, skipping the concurrency storms
	cd backend && pytest -m "not slow"

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	cd backend && pytest --cov=app --cov-report=term-missing --cov-report=html

.PHONY: lint
lint: ## Lint and type-check the backend
	cd backend && ruff check app tests && ruff format --check app tests && mypy app

.PHONY: fmt
fmt: ## Auto-format the backend
	cd backend && ruff format app tests && ruff check --fix app tests

## --------------------------------------------------------------- frontend
.PHONY: web-install
web-install: ## Install frontend dependencies
	cd frontend && npm install

.PHONY: web-dev
web-dev: ## Run the frontend dev server
	cd frontend && npm run dev

.PHONY: web-build
web-build: ## Production build of the frontend
	cd frontend && npm run build

.PHONY: web-lint
web-lint: ## Type-check and lint the frontend
	cd frontend && npm run typecheck && npm run lint

.PHONY: check
check: lint test web-lint ## Everything CI runs
