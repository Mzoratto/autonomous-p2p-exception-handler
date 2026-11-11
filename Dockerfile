FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DEMO_MODE=true \
    P2P_DATABASE_URL=sqlite:////app/data/demo/p2p.db \
    P2P_RULES_PATH=/app/config/business_rules.yaml

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.29 && uv sync --frozen --no-dev
COPY config ./config
COPY .streamlit ./.streamlit
COPY static ./static
COPY app.py ./app.py
RUN python -m p2p.infrastructure.database

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
