.PHONY: install test lint catalog

install:
	uv sync --extra dev --extra postgres --extra qdrant --extra observability

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

# Real products from Amazon Reviews 2023 (streams the 18 GB file, stops after --target products)
catalog:
	uv run shopassist catalog download --target 3000 --out data/catalog.jsonl
