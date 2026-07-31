"""Auction lifecycle scheduler.

Ticks once a second and does two things: promote SCHEDULED auctions whose start
time has passed, and close LIVE auctions whose (possibly extended) end time has
passed.  Both claim work with ``SKIP LOCKED``, so this loop runs safely in every
replica — there is no singleton to fail over and no cron to keep in sync.

An auction is never "closed by a timer" in the correctness sense: the *database*
end time is the truth, and a bid arriving in the gap between due and swept is
rejected by ``place_bid``'s own clock check.  The scheduler only makes the
outcome durable and notifies people.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.services import auction as auction_service

log = get_logger(__name__)


async def tick() -> tuple[int, int]:
    async with session_scope() as session:
        started = await auction_service.start_due_auctions(session)
        closed = await auction_service.close_due_auctions(session)
        await auction_service.refresh_live_gauge(session)
    if started or closed:
        log.info("scheduler.tick", started=started, closed=closed)
    return started, closed


async def run_scheduler(interval: float | None = None) -> None:
    interval = interval or settings.auction_tick_seconds
    log.info("scheduler.started", interval=interval)
    while True:
        try:
            await tick()
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("scheduler.stopped")
            raise
        except Exception as exc:
            log.exception("scheduler.tick_failed", error=str(exc))
            await asyncio.sleep(min(5.0, interval * 5))
