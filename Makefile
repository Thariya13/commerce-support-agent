.PHONY: install test lint

install:
	uv sync --extra dev --extra postgres --extra qdrant --extra observability

test:
	uv run pytest -q

lint:
	uv run ruff check src tests
