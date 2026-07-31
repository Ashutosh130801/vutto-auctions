"""JSON-safe projections shared by the REST layer and the realtime bus.

Keeping one place that decides *what a client is allowed to see* prevents the
classic leak where the WebSocket payload exposes a field the REST response
carefully redacted — here, every bidder's private ``max_amount``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.models.auction import Auction
from app.db.models.bidding import Bid


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def first_image(images: list[str] | None) -> str | None:
    """First photo, or ``None``. Avoids the `(images or [None])[0]` idiom that
    silently types as ``list[str | None]``."""
    return images[0] if images else None


def auction_state(auction: Auction) -> dict[str, Any]:
    """The hot, frequently-changing slice of an auction."""
    return {
        "auction_id": str(auction.id),
        "status": auction.status.value,
        "current_price": f"{auction.current_price:.2f}",
        "minimum_next_bid": f"{auction.minimum_next_bid:.2f}",
        "bid_count": auction.bid_count,
        "bidder_count": auction.bidder_count,
        "leading_user_id": str(auction.leading_user_id) if auction.leading_user_id else None,
        "ends_at": auction.ends_at.isoformat(),
        "extension_count": auction.extension_count,
        "reserve_met": auction.reserve_met,
        "has_reserve": auction.reserve_price is not None,
        "version": auction.version,
    }


def public_bid(bid: Bid, *, bidder_alias: str) -> dict[str, Any]:
    """A bid as *other* people may see it.

    ``max_amount`` is intentionally absent: revealing a rival's ceiling would
    destroy the whole point of proxy bidding.  Bidder identity is reduced to a
    stable per-auction alias.
    """
    return {
        "id": str(bid.id),
        "sequence": bid.sequence,
        "amount": f"{bid.amount:.2f}",
        "status": bid.status.value,
        "source": bid.source.value,
        "bidder_alias": bidder_alias,
        "bidder_id": str(bid.bidder_id),
        "placed_at": bid.placed_at.isoformat(),
        "entry_hash": bid.entry_hash,
    }


def bidder_alias(auction_id: uuid.UUID, bidder_id: uuid.UUID) -> str:
    """Deterministic pseudonym, stable within an auction and unlinkable across
    auctions (the auction id is part of the digest)."""
    import hashlib

    digest = hashlib.sha256(f"{auction_id}:{bidder_id}".encode()).hexdigest()
    return f"Bidder-{digest[:4].upper()}"
