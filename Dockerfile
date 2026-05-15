FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for pyarrow / duckdb wheels are bundled; no apt packages needed.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install -e .

# Bind-mount these at runtime; copies are only for image-only invocations.
COPY configs ./configs
RUN mkdir -p /app/data/raw /app/data/normalised /app/data/account /app/data/derived /app/data/logs

ENTRYPOINT ["python", "-m", "polymarket_arb.cli"]
CMD ["healthcheck"]
