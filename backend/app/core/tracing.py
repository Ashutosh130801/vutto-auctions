"""Optional OpenTelemetry wiring.

Tracing is *opt-in* (``OTEL_ENABLED=true``) and the OTel packages are an extra,
so the base image stays slim and the app boots fine without a collector.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
_initialised = False


def setup_tracing(app: Any, engine: Any = None) -> None:
    global _initialised
    if not settings.otel_enabled or _initialised:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("otel.disabled", reason="opentelemetry extras not installed")
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.app_env,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/health/ready,/metrics")

    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("otel.sqlalchemy_instrument_failed", error=str(exc))

    _initialised = True
    log.info("otel.enabled", endpoint=settings.otel_exporter_otlp_endpoint)


def current_trace_id() -> str | None:
    if not settings.otel_enabled:
        return None
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:  # pragma: no cover
        return None
