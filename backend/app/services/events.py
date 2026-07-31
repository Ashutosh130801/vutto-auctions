"""Domain event names + outbox helper.

Events are appended to the outbox inside the *same* transaction as the state
change they describe, which is what makes "state changed but nobody was told"
impossible.  A relay worker publishes them to Redis for WebSocket fan-out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ops import OutboxEvent


class EventType:
    BID_PLACED = "auction.bid_placed"
    AUCTION_EXTENDED = "auction.extended"
    AUCTION_STARTED = "auction.started"
    AUCTION_ENDED = "auction.ended"
    AUCTION_CANCELLED = "auction.cancelled"
    AUCTION_SETTLED = "auction.settled"
    USER_OUTBID = "user.outbid"


def channel_for(aggregate_type: str, aggregate_id: uuid.UUID | str) -> str:
    """Redis pub/sub channel.  Per-auction channels mean a client watching one
    auction never receives traffic for the other 500 live ones."""
    return f"rt:{aggregate_type}:{aggregate_id}"


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"rt:user:{user_id}"


def emit(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )
    session.add(event)
    return event
