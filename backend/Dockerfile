# syntax=docker/dockerfile:1.7
# Multi-stage. `dev` agrega herramientas de desarrollo; `runtime` es la imagen
# que va a producción: sin toolchain, sin dev-deps, usuario no-root.

# ---------- base ----------
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TZ=America/Santiago

# libpq: psycopg. libgl/glib: dependencias de PyMuPDF/pdfplumber para imágenes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        libglib2.0-0 \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# ---------- deps ----------
# Capa cacheada: solo se invalida si cambian los manifiestos, no el código.
FROM base AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev 2>/dev/null \
    || uv sync --no-install-project --no-dev

# ---------- dev ----------
FROM deps AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project 2>/dev/null || true

ENV PYTHONPATH=/app/src
COPY . /app
CMD ["uvicorn", "dvu.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------- runtime ----------
FROM base AS runtime

COPY --from=deps /opt/venv /opt/venv

RUN groupadd --gid 1000 dvu && \
    useradd --uid 1000 --gid dvu --create-home dvu && \
    mkdir -p /app/data && chown -R dvu:dvu /app

COPY --chown=dvu:dvu alembic.ini /app/alembic.ini
COPY --chown=dvu:dvu alembic /app/alembic
COPY --chown=dvu:dvu src /app/src

ENV PYTHONPATH=/app/src
USER dvu
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "dvu.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
