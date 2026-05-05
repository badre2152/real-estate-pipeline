# Avito Data Pipeline — Makefile
# Usage: make <target>

.PHONY: run test test-integration migrate docker-up docker-down clean-db lint help

run:
	python src/pipeline.py

test:
	pytest src/tests/ -v

test-cov:
	pytest src/tests/ -v --cov=src --cov-report=term-missing

test-integration:
	pytest src/tests/test_integration.py -v

migrate:
	python -c "from src.utils.migrations import run_all_migrations; run_all_migrations()" 

scrape-full:
	@echo "🚀 Starting full scrape — 25 pages (~500 listings)"
	docker-compose up --build

scrape-only:
	@echo "🔍 Running scraper only (no DB required)"
	python -c "from src.extract.scraper import run_scraper; run_scraper(max_pages=25)"

docker-up:
	docker-compose up -d
	@echo "Waiting for PostgreSQL to be ready..."
	@sleep 3
	@echo "DB is up on port 5433"

docker-down:
	docker-compose down

clean-db:
	@echo "Truncating staging and clean tables..."
	python -c "from src.utils.db import execute_query; \
		execute_query('TRUNCATE TABLE staging.raw_annonces RESTART IDENTITY CASCADE;'); \
		execute_query('TRUNCATE TABLE clean.annonces RESTART IDENTITY CASCADE;'); \
		print('Done.')"

lint:
	python -m py_compile src/pipeline.py \
		src/extract/scraper.py \
		src/staging/load_staging.py \
		src/staging/bronze_validator.py \
		src/clean/clean_data.py \
		src/clean/clean_validator.py \
		src/warehouse/bi_schema.py \
		src/warehouse/ml_schema.py \
		src/expectations/gx_bronze.py \
		src/expectations/gx_silver.py \
	&& echo "All files syntax-OK"

rebuild-gx:
	python -m src.expectations.gx_bronze
	python -c "from src.expectations.gx_silver import rebuild_suite; rebuild_suite()"

help:
	@echo "Available targets:"
	@echo "  run         Run the full pipeline"
	@echo "  test        Run all unit tests"
	@echo "  test-cov    Run tests with coverage report"
	@echo "  docker-up   Start PostgreSQL via Docker (port 5433)"
	@echo "  docker-down Stop Docker containers"
	@echo "  clean-db    Truncate staging and clean tables"
	@echo "  lint        Syntax-check all Python source files"
	@echo "  rebuild-gx  Force-rebuild GX expectation suites"