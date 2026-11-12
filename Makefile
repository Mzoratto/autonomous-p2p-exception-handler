.PHONY: setup seed run test coverage lint audit smoke-live

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

# Live model check on a copy of P2P_DATABASE_URL; needs OPENAI_API_KEY in the environment.
smoke-live:
	.venv/bin/python scripts/smoke_live.py

lint:
	.venv/bin/ruff check .

audit:
	uvx pip-audit --path "$$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
