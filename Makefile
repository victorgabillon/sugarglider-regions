.PHONY: check
check:
	uv run --offline ruff format --check .
	uv run --offline ruff check .
	uv run --offline mypy
	uv run --offline pytest
