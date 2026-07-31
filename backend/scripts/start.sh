#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# Single-container entrypoint for platforms that give you one process
# (Render free, Railway, Koyeb, Fly, Cloud Run).
#
# Compose splits migrate / api / worker into separate services, which is the
# right shape when you can run several.  On a one-container free tier we do all
# three here, in order.  Both of the steps before uvicorn are idempotent:
#   * `alembic upgrade head` is a no-op once the schema is current
#   * `app.seed` returns immediately if any user already exists
# so a redeploy or a container restart is safe.
# ---------------------------------------------------------------------------
set -e

# Invoked through the interpreter rather than the `alembic` console script:
# console scripts depend on PATH, and PATH is the first thing a slim image or a
# buildpack loses. `alembic: not found` at deploy time is a bad way to find out.
echo "==> Applying database migrations"
python -m app.cli migrate

if [ "${SEED_ON_START:-true}" = "true" ]; then
  echo "==> Seeding demo data (no-op if the database already has users)"
  python -m app.cli seed
fi

# Free tiers give you a fraction of a CPU, so one worker is correct: extra
# workers would multiply memory (each holds its own connection pool) and buy
# nothing without cores to run them on.
WORKERS="${WEB_CONCURRENCY:-1}"
PORT="${PORT:-8000}"

echo "==> Starting API on :$PORT (workers=$WORKERS)"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$WORKERS" \
  --proxy-headers \
  --forwarded-allow-ips '*' \
  --timeout-keep-alive 65
