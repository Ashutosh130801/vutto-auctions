"""Standalone worker entrypoint: ``python -m app.workers``.

Deployed as its own container so lifecycle processing keeps running (and can be
scaled) independently of HTTP traffic.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.realtime.bus import RealtimeBus
from app.workers.outbox_relay import run_relay
from app.workers.scheduler import run_scheduler

log = get_logger("worker")


async def main() -> None:
    configure_logging(settings.log_level, settings.log_format)
    redis: Redis | None = None
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        redis = client
    except Exception as exc:
        log.warning("worker.redis_unavailable", error=str(exc))
        redis = None

    bus = RealtimeBus(redis)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(run_scheduler(), name="scheduler"),
        asyncio.create_task(run_relay(bus), name="outbox-relay"),
    ]
    log.info("worker.started")
    await stop.wait()
    log.info("worker.stopping")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if redis is not None:
        await redis.aclose()
    await dispose_engine()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
