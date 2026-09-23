# Load optional local settings (WFM_CONFIG_PATH etc.) from .env
-include .env
export

PY := backend/.venv/bin

.PHONY: install dev backend frontend lint typecheck test check

install:
	cd backend && python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend && npm install

dev:
	@trap 'kill 0' EXIT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

backend:
	cd backend && .venv/bin/uvicorn wfm.api.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd backend && .venv/bin/mypy src tests
	cd frontend && npm run typecheck

test:
	cd backend && .venv/bin/pytest -q
	cd frontend && npm test

check: lint typecheck test
