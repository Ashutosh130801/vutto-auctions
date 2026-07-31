"""Realtime layer: the outbox relay, the bus, and the WebSocket protocol.

These matter because the live auction room is the product. A bid that is
correct in the database but never reaches the spectators' screens is, from the
user's point of view, a bid that did not happen.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from app.db.models.ops import OutboxEvent
from app.db.models.user import User
from app.realtime.bus import RealtimeBus
from app.services import bidding as bidding_service
from app.services.events import EventType, channel_for
from app.workers.outbox_relay import relay_once
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _place(sessionmaker_, auction_id, user, amount):
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        result = await bidding_service.place_bid(
            s, auction_id=auction_id, bidder=fresh, max_amount=D(amount)
        )
        await s.commit()
        return result


# ------------------------------------------------------------------ outbox
async def test_a_bid_writes_its_events_in_the_same_transaction(
    sessionmaker_, make_user, make_auction
):
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    await _place(sessionmaker_, auction.id, user, "70000")

    async with sessionmaker_() as s:
        events = (
            (await s.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == auction.id)))
            .scalars()
            .all()
        )
    assert any(e.event_type == EventType.BID_PLACED for e in events)
    assert all(e.dispatched_at is None for e in events), "not dispatched until relayed"


async def test_a_rejected_bid_leaves_no_events_behind(sessionmaker_, make_user, make_auction):
    """Atomicity, from the other direction: a rolled-back bid must not announce
    itself."""
    auction = await make_auction(deposit_required=D("0"))
    a, b = await make_user(deposit=D("0")), await make_user(deposit=D("0"))
    await _place(sessionmaker_, auction.id, a, "70000")

    async with sessionmaker_() as s:
        before = (await s.execute(select(func.count()).select_from(OutboxEvent))).scalar_one()

    from app.core.errors import BidTooLowError

    with pytest.raises(BidTooLowError):
        await _place(sessionmaker_, auction.id, b, "1")

    async with sessionmaker_() as s:
        after = (await s.execute(select(func.count()).select_from(OutboxEvent))).scalar_one()
    assert after == before


async def test_relay_publishes_and_marks_events_dispatched(sessionmaker_, make_user, make_auction):
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    await _place(sessionmaker_, auction.id, user, "70000")

    bus = RealtimeBus(None)
    received: list[dict] = []
    subscription = await bus.subscribe([channel_for("auction", auction.id)])
    async with subscription:
        published = await relay_once(bus)
        assert published > 0
        while (frame := await subscription.get(timeout=0.2)) is not None:
            received.append(frame)

    assert any(f["event"] == EventType.BID_PLACED for f in received)

    async with sessionmaker_() as s:
        pending = (
            await s.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.dispatched_at.is_(None))
            )
        ).scalar_one()
    assert pending == 0


async def test_relay_is_idempotent_across_runs(sessionmaker_, make_user, make_auction):
    """Running the relay twice must not republish — otherwise every extra worker
    replica would multiply traffic."""
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    await _place(sessionmaker_, auction.id, user, "70000")

    bus = RealtimeBus(None)
    first = await relay_once(bus)
    second = await relay_once(bus)
    assert first > 0
    assert second == 0
