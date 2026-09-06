.PHONY: help clean lock test dev dataset backend frontend

help:
	@echo "Targets:"
	@echo "  make clean    - remove caches, build artifacts, and lock-adjacent junk"
	@echo "  make lock     - (re)generate backend (uv.lock) and frontend (package-lock.json) locks"
	@echo "  make test     - run backend (pytest) and frontend (svelte-check/tsc) test suites"
	@echo "  make dev      - run backend (uvicorn --reload) and frontend (vite) dev servers together"
	@echo "  make dataset  - fetch oko.sqlite3/forecast_*.json/exchanges.json from tilalx/oko-dataset"

EXPORT_PATH ?= output/forecast_de.json

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name "__pycache__" -not -path "./.venv/*" -not -path "./frontend/node_modules/*" -exec rm -rf {} +
	rm -rf frontend/dist frontend/.svelte-kit

lock:
	uv lock
	cd frontend && npm install

test:
	uv run pytest
	cd frontend && npm run check

dataset:
	EXPORT_PATH=$(EXPORT_PATH) uv run python -m oko.api.dataset_sync

dev:
	@trap 'kill 0' EXIT INT TERM; \
	EXPORT_PATH=$(EXPORT_PATH) uv run uvicorn oko.api.app:app --reload & \
	cd frontend && npm run dev & \
	wait
