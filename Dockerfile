# syntax=docker/dockerfile:1

# --- Frontend build ---------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Python dependencies ----------------------------------------------------
FROM python:3.12-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- The commit this image is built from (GET /api/version) ----------------
# Read from the checkout's .git without a git binary; only build.json reaches the image.
FROM python:3.12-slim AS revision
COPY backend/app/build.py /build.py
COPY .git /src/.git
RUN python /build.py /src/.git /build.json

# --- Runtime ----------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    BAWBEE_DATABASE_URL="sqlite:////data/bawbee.db" \
    BAWBEE_STATIC_DIR="/app/frontend/dist"
RUN useradd --create-home --uid 1000 bawbee \
    && mkdir -p /data && chown bawbee:bawbee /data \
    && printf '#!/bin/sh\ncd /app/backend && exec python -m app.cli "$@"\n' > /usr/local/bin/bawbee \
    && chmod 755 /usr/local/bin/bawbee
COPY --from=backend-deps /opt/venv /opt/venv
WORKDIR /app/backend
COPY backend/app ./app
COPY backend/config ./config
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY --from=revision /build.json /app/backend/build.json
USER bawbee
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# Migrate on start; the worker waits for the app to be healthy, so it always sees the
# current schema.
CMD ["sh", "-c", "python -m app.cli migrate && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
