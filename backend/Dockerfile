# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Multi-stage build.  The wheels are compiled in a fat builder image and copied
# into a slim runtime, so the shipped image carries no compiler toolchain — a
# smaller attack surface and a much smaller pull.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 # Run as an unprivileged user: a container compromise should not be root.
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# Compose overrides this per service (migrate / api / worker).  Standalone
# platforms that run the image as-is get migrations, seed and the API in one
# go via the entrypoint script.
CMD ["sh", "./scripts/start.sh"]
