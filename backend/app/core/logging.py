"""Structured logging.

Every log line is a structured event.  In production we emit newline-delimited
JSON so a log shipper can index fields directly; locally we render a colourised
console view.  ``request_id`` / ``trace_id`` are bound into a contextvar by the
request-context middleware so *every* log line emitted while handling a request
carries them without the call site having to pass anything around.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)

_NOISY_LOGGERS = ("uvicorn.access", "sqlalchemy.engine.Engine", "asyncio")


def _inject_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if rid := request_id_ctx.get():
        event_dict.setdefault("request_id", rid)
    if uid := user_id_ctx.get():
        event_dict.setdefault("user_id", uid)
    if tid := trace_id_ctx.get():
        event_dict.setdefault("trace_id", tid)
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Idempotently configure structlog + stdlib logging to share one pipeline."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # NB: not `structlog.stdlib.add_logger_name` — that processor reads
        # `logger.name`, which only exists on stdlib loggers. We render through
        # PrintLogger for speed, so the name is bound in `get_logger` instead.
        _inject_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(level.upper()),
        force=True,
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger()
    return logger.bind(logger=name) if name else logger
