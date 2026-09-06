# syntax=docker/dockerfile:1

FROM python:3.14-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --group dev --extra api || \
    uv sync --no-install-project --group dev --extra api

FROM deps AS builder
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --group dev --extra api || uv sync --group dev --extra api

FROM node:26-slim AS frontend-builder
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./

RUN npx vite build --outDir dist --emptyOutDir

FROM builder AS lint
COPY pyproject.toml ./
COPY tests ./tests
RUN uv run ruff check . && uv run ruff format --check . && uv run mypy src

FROM builder AS test
COPY tests ./tests
COPY --from=frontend-builder /fe/dist ./src/oko/api/static
RUN uv run pytest

FROM python:3.14-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git-lfs \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

RUN groupadd --system oko && useradd --system --gid oko --home-dir /app oko && \
    mkdir -p /app/data /output && chown -R oko:oko /app /output

COPY --chown=oko:oko pyproject.toml uv.lock* ./
USER oko
RUN --mount=type=cache,target=/home/oko/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra api || \
    uv sync --no-install-project --no-dev --extra api
COPY --chown=oko:oko README.md ./
COPY --chown=oko:oko src ./src

COPY --chown=oko:oko --from=frontend-builder /fe/dist ./src/oko/api/static

RUN --mount=type=cache,target=/home/oko/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra api --reinstall-package oko || \
    uv sync --no-dev --no-editable --extra api --reinstall-package oko

ENTRYPOINT ["python", "-m"]
CMD ["oko.pipeline", "--export", "/output/forecast_de.json"]

FROM runtime AS serve

ENV DATASET_SYNC_ENABLED=true
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:8000/healthz || exit 1
ENTRYPOINT []
CMD ["sh", "-c", "w=$(nproc); [ \"$w\" -gt 6 ] && w=6; [ \"$w\" -lt 1 ] && w=1; exec python -m uvicorn oko.api.app:app --host 0.0.0.0 --port 8000 --workers \"$w\""]
