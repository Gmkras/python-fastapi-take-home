.PHONY: setup db-up db-down db-reset db-shell dev load test lint

setup:          ## Install dependencies and create .env
	uv sync
	@test -f .env || cp .env.example .env

db-up:          ## Start Postgres in Docker and wait until it is healthy
	docker compose up -d --wait db

db-down:        ## Stop Postgres (keeps data)
	docker compose down

db-reset:       ## Delete all data and re-run db/init/*.sql
	docker compose down -v
	docker compose up -d --wait db

db-shell:       ## Open psql inside the container
	docker compose exec db psql -U postgres -d coffee

dev:            ## Run the API with auto-reload on http://localhost:8000
	uv run fastapi dev app/main.py

load:           ## Run the data loader
	uv run python scripts/load_data.py data/coffee_health_raw.csv

test:           ## Run the test suite (needs the database running)
	uv run pytest

lint:           ## Lint, check formatting and type-check
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
