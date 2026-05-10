# Makefile
# Dev setup
# Code quality
# Docker control
# Logging
# Reports
# Dynamic test runner
# Cleanup
.PHONY: \
	setup install bootstrap \
	test live-tests lint \
	up down reload reload-bridge \
	logs logs-f tail tail-f \
	career-report \
	live-test \
	clean

# ──────── One-Command Setup ────────
setup: install bootstrap
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
install:
	@echo "Creating virtual environment..."
	@$(PYTHON) -m venv .venv --upgrade-deps
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e .[dev]
	@echo "Python environment ready in .venv"

bootstrap:
	@echo "Setting up local config..."
	@./scripts/first_run.sh
	@echo "local/config.json created from example"

# ──────── Core Docker ────────
up: ; docker compose up -d
down: ; docker compose down
reload: ; ./scripts/reload.sh
reload-bridge: ; ./scripts/reload.sh --bridge
career-report: ; $(PYTHON) scripts/career_check.py

# ──────── Logs ────────
logs: ; docker compose logs cortex
logs-f: ; docker compose logs -f cortex
tail: ; docker compose logs --tail=100 cortex
tail-f: ; docker compose logs --tail=100 -f cortex

# ──────── Dev ────────
test: ; ./scripts/pytest.sh
live-tests: ; ./scripts/pytest.sh --live
lint: ; ruff check . && mypy .

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