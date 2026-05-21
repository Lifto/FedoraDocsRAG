.PHONY: help test lint clean

help:
	@echo "Available targets:"
	@echo "  test  - Run tests"
	@echo "  lint  - Run all pre-commit checks (ruff, gitleaks, etc.)"
	@echo "  clean - Remove caches and coverage files"

test:
	uv run pytest

lint:
	uv run pre-commit run --all-files

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
