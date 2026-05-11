# Dockerfile
FROM python:3.12-slim@sha256:ec948fa5f90f4f8907e89f4800cfd2d2e91e391a4bce4a6afa77ba265bc3a2fe

# System deps
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user (getent check makes this idempotent and surfaces real errors)
ARG APP_UID=1000
ARG APP_GID=1000
RUN getent group  app >/dev/null 2>&1 || groupadd  -g "${APP_GID}" app && \
    getent passwd app >/dev/null 2>&1 || useradd -m -u "${APP_UID}" -g "${APP_GID}" -s /bin/bash app

# Layer-cache: install deps before copying source.
# Stubs let hatchling resolve the package so pip can install all deps.
# This layer rebuilds only when pyproject.toml changes.
COPY pyproject.toml ./
ARG INSTALL_DEV=0
RUN mkdir -p modules service scripts \
 && if [ "$INSTALL_DEV" = "1" ]; then \
        pip install --no-cache-dir '.[dev]'; \
    else \
        pip install --no-cache-dir '.'; \
    fi

# Copy real source (invalidated on code changes; deps layer above stays cached)
COPY --chown=app:app modules/ modules/
COPY --chown=app:app service/ service/
COPY --chown=app:app scripts/ scripts/
COPY --chown=app:app tests/ tests/

# Reinstall as editable (--no-deps: all deps already in the cached layer above)
RUN pip install --no-cache-dir --no-deps -e .

# Runtime state directory (bind-mounted from host at run time)
RUN install -d -o app -g app /app/local

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

USER app

# Healthcheck: scheduler writes local/state/heartbeat every 60s; verify it's recent.
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD find /app/local/state/heartbeat -mmin -2 || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "service.cli", "serve"]
