# Makefile — Definition-of-Done runner (harness notes 3, 7, 10).
# No-Docker gates (lint, typecheck, test-unit) run anywhere uv is present.
# Docker gates (dev, test-int, e2e) require a running Docker daemon.

.DEFAULT_GOAL := help
UV := uv

.PHONY: help setup dev down logs lint fmt typecheck test test-unit test-int e2e check check-all \
        demo demo-crash demo-poison demo-duplicate

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install/lock all workspace dependencies
	$(UV) sync --all-packages

dev: ## Build & start the full 6-service docker-compose stack (foreground)
	docker compose up --build

down: ## Stop the stack and remove volumes
	docker compose down -v

logs: ## Tail logs from the running stack
	docker compose logs -f

lint: ## ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-format + apply safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy --strict (config in pyproject.toml)
	$(UV) run mypy

test-unit: ## Unit tests only (no Docker)
	$(UV) run pytest tests/unit

test-int: ## Integration tests — marker 'integration' (needs Docker / testcontainers)
	$(UV) run pytest -m integration

test: ## All pytest tests
	$(UV) run pytest

e2e: ## E2E crash / poison-pill / duplicate tests — marker 'e2e' (needs Docker)
	$(UV) run pytest -m e2e

check: lint typecheck test-unit ## DoD gates runnable WITHOUT Docker
	@echo "OK: lint + typecheck + unit tests passed."
	@echo "Docker gates not run here: make test-int, make e2e (need a Docker daemon)."

check-all: check test-int e2e ## Full DoD: adds integration + E2E (needs Docker)
	@echo "OK: full Definition of Done passed."

demo: ## Narrated resilience demo — all 3 probes, with pauses (for recording)
	@bash demo.sh
demo-crash: ## Demo: crash recovery only
	@bash demo.sh crash
demo-poison: ## Demo: poison-pill -> DLQ only
	@bash demo.sh poison
demo-duplicate: ## Demo: duplicate delivery / idempotency only
	@bash demo.sh duplicate
