# Dockerfile
FROM python:3.12-slim

# System deps
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} app || true && \
    useradd -m -u ${APP_UID} -g ${APP_GID} -s /bin/bash app || true

# --- Copy only what's needed for pip install -e .[dev] ---
# 1. pyproject.toml (required for editable install)
COPY pyproject.toml ./

# 2. Copy source code (modules/, service/, etc.)
COPY modules/ modules/
COPY service/ service/
COPY scripts/ scripts/
COPY tests/ tests/

# 3. Optional: any top-level __init__.py or other root modules
COPY *.py ./

# --- Install editable with dev extras ---
RUN pip install --no-cache-dir -e .[dev]

# --- Secrets (optional) ---
RUN --mount=type=secret,id=env \
    cp /run/secrets/env /app/.env || true

# --- Runtime directories ---
RUN mkdir -p /app/local && chown -R app:app /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "service.cli", "serve"]