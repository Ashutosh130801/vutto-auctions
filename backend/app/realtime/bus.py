"""Realtime fan-out over Redis pub/sub.

Why a bus at all?  Because the API is horizontally scaled: the bidder whose bid
lands on replica 3 must reach the 400 spectators connected to replicas 1, 2 and
4.  Redis pub/sub decouples "who accepted the bid" from "who is watching", so no
sticky sessions and no replica-to-replica awareness are needed.

Delivery semantics are *at-least-once* and *best-effort ordering*.  Clients
therefore treat every frame as idempotent and reconcile using the monotonic
``auction.version`` — a frame carrying an older version than the one already
rendered is simply dropped.  The authoritative state is always the database; the
bus is an accelerator, never the source of truth.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger(__name__)


class RealtimeBus:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._local: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    # -- publish -----------------------------------------------------------
    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._deliver_local(channel, message)
        if self._redis is None:
            return
        try:
            await self._redis.publish(channel, json.dumps(message, default=str))
        except Exception as exc:  # pragma: no cover - resilience path
            log.warning("bus.publish_failed", channel=channel, error=str(exc))

    async def _deliver_local(self, channel: str, message: dict[str, Any]) -> None:
        """Deliver to subscribers on *this* process without a Redis round trip.

        Also the reason single-node development works with Redis switched off.
        """
        async with self._lock:
            queues = list(self._local.get(channel, ()))
        for queue in queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A client too slow to drain gets dropped rather than being
                # allowed to apply backpressure to the whole auction.
                log.warning("bus.subscriber_slow_dropped", channel=channel)

    # -- subscribe ---------------------------------------------------------
    async def subscribe(self, channels: list[str]) -> Subscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            for channel in channels:
                self._local.setdefault(channel, set()).add(queue)
        pubsub = None
        if self._redis is not None:
            try:
                pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(*channels)
            except Exception as exc:  # pragma: no cover
                log.warning("bus.subscribe_failed", error=str(exc))
                pubsub = None
        return Subscription(self, channels, queue, pubsub)

    async def _unsubscribe(self, channels: list[str], queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            for channel in channels:
                subs = self._local.get(channel)
                if subs:
                    subs.discard(queue)
                    if not subs:
                        self._local.pop(channel, None)


class Subscription:
    def __init__(
        self,
        bus: RealtimeBus,
        channels: list[str],
        queue: asyncio.Queue[dict[str, Any]],
        pubsub: Any,
    ) -> None:
        self._bus = bus
        self._channels = channels
        self._queue = queue
        self._pubsub = pubsub
        self._pump: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Subscription:
        if self._pubsub is not None:
            self._pump = asyncio.create_task(self._pump_redis())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._pump is not None:
            self._pump.cancel()
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(*self._channels)
                await self._pubsub.aclose()
            except Exception as exc:  # pragma: no cover - teardown is best effort
                log.debug("bus.unsubscribe_failed", error=str(exc))
        await self._bus._unsubscribe(self._channels, self._queue)

    async def _pump_redis(self) -> None:  # pragma: no cover - needs a live Redis
        try:
            async for raw in self._pubsub.listen():
                if raw is None or raw.get("type") != "message":
                    continue
                try:
                    payload = json.loads(raw["data"])
                except (ValueError, TypeError):
                    continue
                if payload.get("_origin") == id(self._bus):
                    continue  # already delivered locally; avoid duplicate frames
                try:
                    self._queue.put_nowait(payload)
                except asyncio.QueueFull:
                    log.warning("bus.subscriber_slow_dropped")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("bus.pump_failed", error=str(exc))

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._queue.get()

    async def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
