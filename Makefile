.PHONY: test test-cov test-fast test-docker lint format clean help

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

test:  ## Run all tests with coverage
	pytest -v --cov=src/open_orchestrator --cov-report=term-missing --cov-report=html --cov-report=xml

test-cov:  ## Run tests and open HTML coverage report
	pytest -v --cov=src/open_orchestrator --cov-report=html
	@echo "Opening coverage report..."
	@open htmlcov/index.html || xdg-open htmlcov/index.html || echo "Please open htmlcov/index.html manually"

test-fast:  ## Run tests excluding slow tests
	pytest -v -m "not slow"

test-docker:  ## Run tests in Docker container
	docker compose -f docker-compose.test.yml up --build

test-docker-interactive:  ## Start interactive Docker test environment
	docker compose -f docker-compose.test.yml run --rm test-interactive

lint:  ## Run linting checks
	ruff check src/ tests/
	mypy src/

format:  ## Format code with ruff
	ruff format src/ tests/

clean:  ## Clean up test artifacts
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

install:  ## Install package with dev dependencies
	pip install -e ".[dev]"

install-uv:  ## Install package with dev dependencies using uv
	uv pip install -e ".[dev]"
