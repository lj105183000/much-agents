# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Python runtime shared by the API, workers, and migration job.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS api-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    HOME=/tmp \
    PATH="/app/.venv/bin:$PATH"

# Optional Debian mirror for restricted networks, e.g.
#   --build-arg APT_MIRROR=mirrors.aliyun.com
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        find /etc/apt -type f \( -name '*.sources' -o -name 'sources.list' \) \
            -exec sed -i \
                "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
                {} + ; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# Install the pinned uv release from PyPI. ghcr.io is not always reachable
# from internal networks; pass --build-arg PIP_INDEX_URL=<mirror> if needed.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --disable-pip-version-check \
    --root-user-action=ignore --index-url "$PIP_INDEX_URL" uv==0.12.12

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /tmp --shell /usr/sbin/nologin app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY api ./api
COPY src ./src
COPY scripts/worker_healthcheck.py ./scripts/worker_healthcheck.py

RUN mkdir -p /app/upload \
    && chown -R app:app /app

USER app

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
FROM api-base AS api

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/api/live || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]

# ---------------------------------------------------------------------------
# Celery worker (queue and concurrency are supplied by Compose)
# ---------------------------------------------------------------------------
FROM api-base AS worker

CMD ["celery", "-A", "api.celery_app:celery_app", "worker", \
     "-Q", "parse", "--pool=prefork", "--concurrency=1", "--loglevel=INFO"]

# ---------------------------------------------------------------------------
# One-shot Alembic migration job
# ---------------------------------------------------------------------------
FROM api-base AS migrate

CMD ["alembic", "upgrade", "head"]

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web-build

# Optional npm registry for restricted networks, e.g.
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --registry "$NPM_REGISTRY"
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Nginx with TLS termination and the built SPA
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS web

COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /app/dist /usr/share/nginx/html

EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
