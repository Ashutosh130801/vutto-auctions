"""WebSocket connection manager.

Responsibilities kept deliberately narrow: accept a socket, subscribe it to the
right bus channels, forward frames, and keep the connection healthy with a
server-driven heartbeat that also carries ``server_time``.

The heartbeat doing double duty is the trick behind the countdown UX: clients
compute ``offset = server_time - client_time`` on every frame and render the
remaining time from the corrected clock.  A user whose laptop clock is 40
seconds fast still sees the *auction's* truth, which matters a great deal when
the last two minutes decide who wins.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from app.core import metrics
from app.core.logging import get_logger
from app.realtime.bus import RealtimeBus

log = get_logger(__name__)

HEARTBEAT_SECONDS = 15.0


class ConnectionManager:
    """Tracks viewer presence per auction so the UI can show "N watching"."""

    def __init__(self) -> None:
        self._viewers: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def join(self, auction_id: str, connection_id: str) -> int:
        async with self._lock:
            self._viewers.setdefault(auction_id, set()).add(connection_id)
            return len(self._viewers[auction_id])

    async def leave(self, auction_id: str, connection_id: str) -> int:
        async with self._lock:
            viewers = self._viewers.get(auction_id)
            if not viewers:
                return 0
            viewers.discard(connection_id)
            count = len(viewers)
            if not viewers:
                self._viewers.pop(auction_id, None)
            return count

    def count(self, auction_id: str) -> int:
        return len(self._viewers.get(auction_id, ()))


manager = ConnectionManager()


def envelope(event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": event,
        "data": data or {},
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


async def serve_auction_socket(
    websocket: WebSocket,
    *,
    bus: RealtimeBus,
    auction_id: uuid.UUID,
    user_id: uuid.UUID | None,
    initial_state: dict[str, Any],
) -> None:
    from app.services.events import channel_for, user_channel

    connection_id = uuid.uuid4().hex
    channels = [channel_for("auction", auction_id)]
    if user_id is not None:
        channels.append(user_channel(user_id))

    await websocket.accept()
    metrics.ws_connections.inc()
    viewers = await manager.join(str(auction_id), connection_id)

    async with await bus.subscribe(channels) as subscription:
        await _send(websocket, envelope("snapshot", {**initial_state, "viewers": viewers}))
        await bus.publish(
            channels[0], envelope("presence", {"viewers": viewers, "auction_id": str(auction_id)})
        )

        reader = asyncio.create_task(_drain_client(websocket))
        last_beat = time.monotonic()
        try:
            while not reader.done():
                message = await subscription.get(timeout=1.0)
                if message is not None:
                    await _send(websocket, message)
                    continue
                if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                    await _send(
                        websocket,
                        envelope(
                            "heartbeat",
                            {"viewers": manager.count(str(auction_id))},
                        ),
                    )
                    last_beat = time.monotonic()
        except Exception as exc:
            log.info("ws.closed", reason=str(exc), auction_id=str(auction_id))
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            metrics.ws_connections.dec()
            remaining = await manager.leave(str(auction_id), connection_id)
            await bus.publish(
                channels[0],
                envelope("presence", {"viewers": remaining, "auction_id": str(auction_id)}),
            )


async def _drain_client(websocket: WebSocket) -> None:
    """Read (and mostly ignore) client frames.

    The socket is push-only by design — bids go through the authenticated REST
    endpoint so they get the full middleware stack (rate limiting, idempotency,
    audit).  Reading is still necessary to observe disconnects promptly and to
    answer application-level pings.
    """
    while True:
        raw = await websocket.receive_text()
        if raw and '"ping"' in raw:
            await _send(websocket, envelope("pong"))


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_json(payload)
    metrics.ws_messages_sent_total.labels(event=payload.get("event", "unknown")).inc()
