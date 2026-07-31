"""Liveness, readiness and metrics.

The distinction matters to an orchestrator:

* ``/health``       — is the process alive?  Never touches a dependency, so a
  slow database can never cause Kubernetes to kill an otherwise healthy pod.
* ``/health/ready`` — can it *serve*?  Checks Postgres and Redis, so a pod with
  a broken dependency is pulled out of the load balancer instead of returning
  errors to users.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core import metrics
from app.core.config import settings
from app.db.session import get_sessionmaker

router = APIRouter(tags=["ops"])
_BOOTED_AT = time.time()


@router.get("/health", summary="Liveness probe", include_in_schema=False)
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.otel_service_name,
        "env": settings.app_env,
        "uptime_seconds": round(time.time() - _BOOTED_AT, 1),
    }


@router.get("/health/ready", summary="Readiness probe", include_in_schema=False)
async def ready(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    healthy = True

    started = time.perf_counter()
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        healthy = False
        checks["database"] = {"status": "error", "error": str(exc)[:200]}

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        # Redis is an accelerator, not a hard dependency: without it the app
        # still serves correct data, just with single-node realtime fan-out.
        checks["redis"] = {"status": "disabled"}
    else:
        started = time.perf_counter()
        try:
            await redis.ping()
            checks["redis"] = {
                "status": "ok",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:
            checks["redis"] = {"status": "degraded", "error": str(exc)[:200]}

    response.status_code = 200 if healthy else 503
    return {"status": "ready" if healthy else "not_ready", "checks": checks}


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    if not settings.metrics_enabled:
        return Response(status_code=404)
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)
