# Pinned by version and digest; bump both together.
FROM ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc AS uv

FROM python:3.11.18-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    DEMO_MODE=true \
    P2P_DATABASE_URL=sqlite:////app/data/demo/p2p.db \
    P2P_RULES_PATH=/app/config/business_rules.yaml

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so source edits do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY config ./config
COPY .streamlit ./.streamlit
COPY static ./static
COPY app.py ./app.py

# The app runs unprivileged; only the data directory is writable, and it holds the
# database, checkpoints, WAL files and the command lock file.
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app \
    && mkdir -p /app/data/demo \
    && chown -R app:app /app/data
USER app
RUN python -m p2p.infrastructure.database

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
