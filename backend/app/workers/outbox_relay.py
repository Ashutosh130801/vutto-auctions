"""Outbox relay: database → Redis pub/sub.

Runs in every API process *and* in the dedicated worker container.  Claiming
rows with ``FOR UPDATE SKIP LOCKED`` means running N copies is safe and
increases throughput rather than causing duplicate publishes.

Failure handling: a row that cannot be published keeps ``dispatched_at IS NULL``
and its ``attempts`` counter grows.  The ``outbox_pending`` gauge is the alert
signal — if it climbs, realtime is degraded but *no data has been lost*, and the
backlog drains automatically once the bus recovers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.ops import OutboxEvent
from app.db.session import session_scope
from app.realtime.bus import RealtimeBus
from app.services.events import channel_for

log = get_logger(__name__)


async def relay_once(bus: RealtimeBus, *, batch: int | None = None) -> int:
    batch = batch or settings.outbox_relay_batch
    published = 0
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.dispatched_at.is_(None))
                    .order_by(OutboxEvent.created_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        for event in rows:
            channel = channel_for(event.aggregate_type, event.aggregate_id)
            try:
                await bus.publish(
                    channel,
                    {
                        "event": event.event_type,
                        "data": event.payload,
                        "event_id": str(event.id),
                        "server_time": now.isoformat(),
                    },
                )
                event.dispatched_at = now
                metrics.outbox_dispatched_total.labels(event_type=event.event_type).inc()
                published += 1
            except Exception as exc:  # pragma: no cover - resilience path
                event.attempts += 1
                event.last_error = str(exc)[:500]
                log.warning("outbox.publish_failed", event_id=str(event.id), error=str(exc))

        pending = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.dispatched_at.is_(None))
            )
        ).scalar_one()
        metrics.outbox_pending.set(pending)
    return published


async def run_relay(bus: RealtimeBus, *, interval: float = 0.25) -> None:
    log.info("outbox_relay.started", interval=interval)
    while True:
        try:
            published = await relay_once(bus)
            # Drain aggressively while there is a backlog, idle politely when not.
            await asyncio.sleep(0 if published >= settings.outbox_relay_batch else interval)
        except asyncio.CancelledError:
            log.info("outbox_relay.stopped")
            raise
        except Exception as exc:
            log.exception("outbox_relay.iteration_failed", error=str(exc))
            await asyncio.sleep(1.0)
