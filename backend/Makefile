# Local and CI checks run the same commands (A§26.6). Every target fails on the first failing tool.
.PHONY: check format lint types imports test

check: format lint types imports test

format:
	uv run ruff format --check .

lint:
	uv run ruff check .

types:
	uv run mypy

imports:
	uv run lint-imports

test:
	uv run pytest
