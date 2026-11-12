.PHONY: setup seed run test coverage lint typecheck audit smoke-live

setup:
	uv sync --frozen --extra dev --python 3.11

seed:
	.venv/bin/python -m p2p.infrastructure.database

# Local only by default: `make run ADDRESS=0.0.0.0` exposes the unauthenticated dashboard to
# the network. The Docker image binds 0.0.0.0 itself; Compose publishes it on loopback.
ADDRESS ?= localhost

run:
	.venv/bin/streamlit run app.py --server.headless true --browser.gatherUsageStats false --server.address $(ADDRESS)

test:
	.venv/bin/pytest

coverage:
	.venv/bin/pytest --cov --cov-report=term

# Live model check on fresh demo seeds (add ARGS=--from-db to use a copy of P2P_DATABASE_URL);
# needs OPENAI_API_KEY in the environment.
smoke-live:
	.venv/bin/python scripts/smoke_live.py $(ARGS)

lint:
	.venv/bin/ruff check .

# Pyright in standard mode on src/, pinned in the dev extra; settings in [tool.pyright].
typecheck:
	.venv/bin/pyright

audit:
	uvx pip-audit --path "$$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
