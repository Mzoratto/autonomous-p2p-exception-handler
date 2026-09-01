.PHONY: setup seed run test lint

setup:
	uv venv --python 3.11
	uv pip install --python .venv/bin/python -e '.[dev]'

seed:
	.venv/bin/python -m p2p.infrastructure.database

run:
	.venv/bin/streamlit run app.py

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .
