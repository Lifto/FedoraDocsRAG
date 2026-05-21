.PHONY: test lint format clean help

test:
	uv run pytest

lint:
	uv run ruff check --fix .

format:
	uv run ruff format .

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage

help:
	@echo "Available targets:"
	@echo "  test     - Run tests"
	@echo "  lint     - Run ruff linter with auto-fix"
	@echo "  format   - Run ruff formatter"
	@echo "  clean    - Remove caches and coverage files"
