"""HTTP middleware: request context, metrics, security headers, body limits."""

from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger, request_id_ctx, trace_id_ctx, user_id_ctx
from app.core.tracing import current_trace_id

log = get_logger("http")

MAX_BODY_BYTES = 1_048_576  # 1 MiB — no endpoint here needs more


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates a request id and emits one access log line per request.

    Honouring an inbound ``X-Request-ID`` lets a trace span the load balancer,
    this service and anything it calls, which is what makes a support ticket
    ("my bid failed at 14:32") answerable with a single log query.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request_id_ctx.set(request_id)
        trace_id_ctx.set(current_trace_id())
        user_id_ctx.set(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            log.exception(
                "http.request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration * 1000, 2),
            )
            raise

        duration = time.perf_counter() - started
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration * 1000:.1f}"

        route = _route_template(request)
        if settings.metrics_enabled and route != "/metrics":
            metrics.http_requests_total.labels(
                method=request.method, route=route, status=str(response.status_code)
            ).inc()
            metrics.http_request_duration_seconds.labels(
                method=request.method, route=route
            ).observe(duration)

        if route not in ("/health", "/health/ready", "/metrics"):
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                route=route,
                status=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )
        return response


def _route_template(request: Request) -> str:
    """Use the *route pattern* (``/auctions/{id}``) not the concrete path, or
    Prometheus cardinality explodes with one series per auction."""
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # This service returns JSON only; a maximally restrictive CSP costs
        # nothing and neutralises any reflected-content surprise.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds {MAX_BODY_BYTES} bytes.",
                    }
                },
            )
        return await call_next(request)
