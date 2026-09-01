# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
# libgomp1: OpenMP runtime required by lightgbm's native library.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# ---- deps: resolve + install dependencies only (best layer-cache hit rate) --
FROM base AS deps
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --group dev --extra api || \
    uv sync --no-install-project --group dev --extra api

# ---- builder: install the project itself on top of resolved deps ----------
FROM deps AS builder
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --group dev --extra api || uv sync --group dev --extra api

# ---- frontend-builder: Svelte web UI, built independently of the Python
# chain above (different toolchain entirely) -- see frontend/README or
# `oko.api.app`'s STATIC_DIR for where the output ends up.
FROM node:22-slim AS frontend-builder
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts's build.outDir points straight at src/oko/api/static, but
# that path doesn't exist in this stage's filesystem (only frontend/ was
# copied in) -- override it to a local dir and copy that into `runtime`
# below instead.
RUN npx vite build --outDir dist --emptyOutDir

# ---- lint: ruff + mypy, exits non-zero on violations -----------------------
FROM builder AS lint
COPY pyproject.toml ./
COPY tests ./tests
RUN uv run ruff check . && uv run ruff format --check . && uv run mypy src

# ---- test: pytest with coverage --------------------------------------------
FROM builder AS test
COPY tests ./tests
RUN uv run pytest

# ---- runtime: minimal, non-root, no dev/test deps --------------------------
FROM python:3.12-slim AS runtime
# libgomp1: OpenMP runtime required by lightgbm's native library.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

RUN groupadd --system oko && useradd --system --gid oko --home-dir /app oko

COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra api || \
    uv sync --no-install-project --no-dev --extra api
COPY README.md ./
COPY src ./src
# Overwrites the checked-in zones.geojson's sibling files (index.html,
# hashed assets/*) with the freshly built web UI -- zones.geojson itself
# isn't produced by the frontend build, so it's untouched.
COPY --from=frontend-builder /fe/dist ./src/oko/api/static
# --reinstall-package oko: the non-editable wheel build for the local
# project must never be served from a stale uv build-cache entry keyed
# loosely enough to survive a source change at the same version number
# (observed once in development) -- everything else stays cached.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra api --reinstall-package oko || \
    uv sync --no-dev --no-editable --extra api --reinstall-package oko

RUN mkdir -p /app/data /output && chown -R oko:oko /app /output

USER oko

ENTRYPOINT ["python", "-m"]
CMD ["oko.pipeline", "--export", "/output/forecast_de.json"]

# ---- serve: FastAPI query layer (web UI + evcc endpoint), long-lived -----
# Built on top of `runtime` (already has the `api` extra installed) rather
# than an independent chain -- it reads /output/forecast_de.json live from
# a mounted volume (see docker-compose.yml / Jenkinsfile), so unlike the
# old static-file approach it does not need that file to exist at build
# time, and does not need rebuilding+redeploying just because a new
# forecast landed. Port 8000, not 80: `runtime` (and thus this stage) runs
# as the non-root `oko` user, which can't bind a privileged port.
FROM runtime AS serve
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "oko.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
