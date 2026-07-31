"""FastAPI application factory.

Composition root: everything the app needs is constructed here and hung off
``app.state``, so tests can swap any of it out without monkey-patching imports.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import health
from app.api.v1 import admin, auctions, auth, me
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import RateLimiter
from app.core.tracing import setup_tracing
from app.db.session import dispose_engine, get_engine
from app.realtime.bus import RealtimeBus

log = get_logger("app")

DESCRIPTION = """
Live auction platform for used motorcycles.

**Bidding is proxy-based.** You submit the *maximum* you are willing to pay and
the engine bids on your behalf in increments, only as high as needed to lead.

**Auctions close softly.** A bid placed inside the anti-snipe window pushes the
end time out, so a last-second snipe cannot deny other bidders a response.

**Every bid is in a tamper-evident ledger.** Bids are hash-chained;
`GET /api/v1/auctions/{id}/ledger` re-verifies the whole chain on demand.

Errors share one envelope:
`{"error": {"code": "...", "message": "...", "details": {...}}, "request_id": "..."}`
Branch on `code`, never on `message`.
"""


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level, settings.log_format)
    log.info("app.starting", env=settings.app_env, version=app.version)

    redis: Redis | None = None
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        redis = client
        log.info("redis.connected")
    except Exception as exc:
        # Degrade, don't die: single-node realtime + no distributed rate limit.
        log.warning("redis.unavailable", error=str(exc))
        redis = None

    app.state.redis = redis
    app.state.bus = RealtimeBus(redis)
    app.state.rate_limiter = RateLimiter(redis)
    app.state.trust_proxy_headers = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

    setup_tracing(app, get_engine())

    background: list[asyncio.Task[Any]] = []
    if os.getenv("RUN_BACKGROUND_WORKERS", "true").lower() == "true":
        # In-process workers keep `docker compose up` and local dev to one
        # command; production also runs the dedicated worker container, and the
        # SKIP LOCKED claiming makes that duplication harmless.
        from app.workers.outbox_relay import run_relay
        from app.workers.scheduler import run_scheduler

        background = [
            asyncio.create_task(run_scheduler(), name="scheduler"),
            asyncio.create_task(run_relay(app.state.bus), name="outbox-relay"),
        ]
    app.state.background_tasks = background

    try:
        yield
    finally:
        log.info("app.stopping")
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()
        await dispose_engine()
        log.info("app.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
    )

    # Order matters: outermost first.  Security headers wrap everything so they
    # are present even on responses produced by an exception handler.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing"],
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(auctions.router, prefix=settings.api_v1_prefix)
    app.include_router(me.router, prefix=settings.api_v1_prefix)
    app.include_router(admin.router, prefix=settings.api_v1_prefix)

    _install_exception_handlers(app)
    return app


def _install_exception_handlers(app: FastAPI) -> None:
    def envelope(request: Request, code: str, message: str, details: dict | None = None) -> dict:
        body: dict[str, Any] = {"error": {"code": code, "message": message}}
        if details:
            body["error"]["details"] = details
        body["request_id"] = getattr(request.state, "request_id", None)
        return body

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("error.app", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(request, exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Flatten Pydantic's nested errors into something a UI can render next
        # to the offending field without any client-side interpretation.
        fields = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p not in ("body", "query")),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=envelope(
                request, "VALIDATION_ERROR", "Request failed validation.", {"fields": fields}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(request, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals to a client; the request id is the bridge to the
        # full stack trace in the logs.
        log.exception("error.unhandled", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content=envelope(request, "INTERNAL_ERROR", "An unexpected error occurred."),
        )


app = create_app()
