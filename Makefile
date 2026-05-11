# Makefile
.DEFAULT_GOAL := help

.PHONY: \
	help \
	setup install bootstrap \
	test live-tests lint format \
	up down reload reload-bridge rebuild \
	logs logs-f tail tail-f \
	career-report \
	live-test \
	clean

# ──────── Help ────────
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# ──────── One-Command Setup ────────
setup: install bootstrap ## Create venv, install deps, bootstrap local config
	@echo ""
	@echo "Setup complete!"
	@echo "   .venv → ready"
	@echo "   local/config.json → ready"
	@echo ""
	@echo "Next: docker compose up -d"

# ──────── Python detection (python3 > python) ────────
PYTHON := $(or $(shell command -v python3 2>/dev/null), \
               $(shell command -v python 2>/dev/null), \
               $(error Python not found! Install python3 + python3-venv))

# ──────── Core ────────
install: ## Create .venv and install all dependencies (including dev)
	@echo "Creating virtual environment..."
	@$(PYTHON) -m venv .venv --upgrade-deps
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e .[dev]
	@echo "Python environment ready in .venv"

bootstrap: ## Copy example configs into local/ if not present
	@echo "Setting up local config..."
	@./scripts/first_run.sh
	@echo "local/config.json created from example"

# ──────── Core Docker ────────
up: ; docker compose up -d ## Start all services
down: ; docker compose down ## Stop all services
reload: ; ./scripts/reload.sh ## Rebuild and restart cortex container
reload-bridge: ; ./scripts/reload.sh --bridge ## Rebuild and restart bridge + cortex
rebuild: ; docker compose build --pull && docker compose up -d --force-recreate ## Pull base images, rebuild, and restart
career-report: ; $(PYTHON) scripts/career_check.py ## Run career report script locally

# ──────── Logs ────────
logs: ; docker compose logs cortex ## Show cortex logs
logs-f: ; docker compose logs -f cortex ## Follow cortex logs
tail: ; docker compose logs --tail=100 cortex ## Last 100 lines of cortex logs
tail-f: ; docker compose logs --tail=100 -f cortex ## Last 100 lines, then follow

# ──────── Dev ────────
test: ; ./scripts/pytest.sh ## Run test suite in container
live-tests: ; ./scripts/pytest.sh --live ## Run live tests in container
lint: ; .venv/bin/ruff check . && .venv/bin/mypy . ## Lint and type-check
format: ; .venv/bin/ruff format . ## Auto-format all Python files

# -------------------------------------------------
# LIVE TEST - make live-test <keyword>
# -------------------------------------------------
live-test:
	@if [ -z "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
	  echo "Usage: make live-test <keyword>"; \
	  echo "  e.g. make live-test bae"; \
	  exit 1; \
	fi; \
	keyword=$$(echo $(filter-out $@,$(MAKECMDGOALS)) | head -1); \
	test_file=$$(find tests/career_live tests/assorted_live -type f -name "*$$keyword*.py" -print -quit 2>/dev/null); \
	if [ -z "$$test_file" ]; then \
	  echo "Error: No live test file matching '$$keyword' found in career_live/ or assorted_live/"; \
	  exit 1; \
	fi; \
	echo "Running: pytest --live -vv -s $$test_file"; \
	docker compose run --rm cortex pytest --live -vv -s "$$test_file"

# ──────── Cleanup ────────
clean: ; docker compose down -v --remove-orphans

# Allow extra args
%::
	@: