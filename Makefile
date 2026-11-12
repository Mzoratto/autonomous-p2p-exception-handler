.PHONY: setup seed run test coverage lint audit

setup:
	uv sync --frozen --extra dev --python 3.11

seed:
	.venv/bin/python -m p2p.infrastructure.database

run:
	.venv/bin/streamlit run app.py

test:
	.venv/bin/pytest

coverage:
	.venv/bin/pytest --cov --cov-report=term

lint:
	.venv/bin/ruff check .

audit:
	uvx pip-audit --path "$$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
