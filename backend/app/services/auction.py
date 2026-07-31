"""Auction lifecycle: creation, start, close, settle, cancel.

Closing is the other place where concurrency bites: N scheduler replicas will
all notice the same auction is due.  ``close_due_auctions`` therefore claims
work with ``FOR UPDATE SKIP LOCKED``, which hands each due auction to exactly
one worker and lets the others move straight on to the next row instead of
queueing behind it.  No leader election, no distributed lock, no cron singleton.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models.auction import Auction
from app.db.models.bidding import Bid
from app.db.models.catalog import Bike
from app.db.models.enums import (
    AuctionOutcome,
    AuctionStatus,
    BidStatus,
    BikeStatus,
    DepositTxnType,
    HoldStatus,
    NotificationType,
)
from app.db.models.finance import DepositAccount, DepositHold, DepositTransaction
from app.db.models.ops import Notification
from app.services import events, serializers

log = get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, suffix: str | None = None) -> str:
    base = _SLUG_STRIP.sub("-", value.lower()).strip("-")[:120]
    return f"{base}-{suffix}" if suffix else base


async def create_auction(
    session: AsyncSession,
    *,
    bike_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    start_price: Decimal,
    bid_increment: Decimal,
    reserve_price: Decimal | None,
    deposit_required: Decimal,
    anti_snipe_window_seconds: int,
    anti_snipe_extension_seconds: int,
    anti_snipe_max_extensions: int,
    notes: str | None,
    created_by: uuid.UUID | None,
) -> Auction:
    bike = (
        await session.execute(select(Bike).where(Bike.id == bike_id).with_for_update())
    ).scalar_one_or_none()
    if bike is None:
        raise NotFoundError("Bike not found.")
    if bike.status not in (BikeStatus.READY, BikeStatus.DRAFT):
        raise ConflictError(
            f"Bike is {bike.status.value}; only DRAFT or READY bikes can be auctioned."
        )
    if ends_at <= starts_at:
        raise ValidationError("ends_at must be after starts_at.")
    if reserve_price is not None and reserve_price < start_price:
        raise ValidationError("reserve_price cannot be below start_price.")

    auction = Auction(
        bike_id=bike.id,
        slug=slugify(bike.title, uuid.uuid4().hex[:6]),
        title=bike.title,
        notes=notes,
        status=AuctionStatus.SCHEDULED,
        starts_at=starts_at,
        ends_at=ends_at,
        scheduled_ends_at=ends_at,
        start_price=start_price,
        reserve_price=reserve_price,
        bid_increment=bid_increment,
        deposit_required=deposit_required,
        current_price=start_price,
        anti_snipe_window_seconds=anti_snipe_window_seconds,
        anti_snipe_extension_seconds=anti_snipe_extension_seconds,
        anti_snipe_max_extensions=anti_snipe_max_extensions,
        created_by=created_by,
    )
    session.add(auction)
    bike.status = BikeStatus.IN_AUCTION
    await session.flush([auction])
    log.info("auction.created", auction_id=str(auction.id), bike_id=str(bike.id))
    return auction


async def start_due_auctions(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Flip SCHEDULED → LIVE for everything whose start time has passed."""
    now = now or datetime.now(timezone.utc)
    rows = (
        (
            await session.execute(
                select(Auction)
                .where(Auction.status == AuctionStatus.SCHEDULED, Auction.starts_at <= now)
                .order_by(Auction.starts_at)
                .limit(200)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for auction in rows:
        auction.status = AuctionStatus.LIVE
        metrics.auction_transitions_total.labels(to_status="LIVE").inc()
        events.emit(
            session,
            aggregate_type="auction",
            aggregate_id=auction.id,
            event_type=events.EventType.AUCTION_STARTED,
            payload={"auction": serializers.auction_state(auction)},
        )
        log.info("auction.started", auction_id=str(auction.id))
    return len(rows)


async def close_due_auctions(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Close every LIVE auction past its (possibly extended) end time.

    ``SKIP LOCKED`` makes this safe to run on every replica simultaneously.
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        (
            await session.execute(
                select(Auction)
                .where(Auction.status == AuctionStatus.LIVE, Auction.ends_at <= now)
                .order_by(Auction.ends_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for auction in rows:
        await close_auction(session, auction, now=now)
    return len(rows)


async def close_auction(
    session: AsyncSession, auction: Auction, *, now: datetime | None = None
) -> Auction:
    """Determine the outcome, settle deposits, notify participants.

    Caller must already hold the row lock (``close_due_auctions`` does).
    """
    now = now or datetime.now(timezone.utc)
    if auction.status not in (AuctionStatus.LIVE, AuctionStatus.SCHEDULED):
        return auction

    auction.status = AuctionStatus.ENDED
    auction.closed_at = now

    if auction.bid_count == 0 or auction.leading_bid_id is None:
        auction.outcome = AuctionOutcome.NO_BIDS
    elif not auction.reserve_met:
        auction.outcome = AuctionOutcome.RESERVE_NOT_MET
    else:
        auction.outcome = AuctionOutcome.SOLD
        auction.winner_id = auction.leading_user_id
        auction.winning_amount = auction.current_price

    # Ledger bookkeeping: the leading bid becomes WON (or LOST if unsold), all
    # other bids become LOST.  One statement each, no N+1.
    if auction.leading_bid_id is not None:
        await session.execute(
            update(Bid)
            .where(Bid.id == auction.leading_bid_id)
            .values(
                status=(
                    BidStatus.WON if auction.outcome == AuctionOutcome.SOLD else BidStatus.LOST
                ),
                is_winning=auction.outcome == AuctionOutcome.SOLD,
            )
        )
    await session.execute(
        update(Bid)
        .where(
            Bid.auction_id == auction.id,
            Bid.id != (auction.leading_bid_id or uuid.UUID(int=0)),
            Bid.status.in_([BidStatus.LEADING, BidStatus.OUTBID]),
        )
        .values(status=BidStatus.LOST)
    )

    await _settle_holds(session, auction, now=now)
    await _notify_participants(session, auction, now=now)

    if auction.outcome == AuctionOutcome.SOLD:
        await session.execute(
            update(Bike).where(Bike.id == auction.bike_id).values(status=BikeStatus.SOLD)
        )
    else:
        await session.execute(
            update(Bike).where(Bike.id == auction.bike_id).values(status=BikeStatus.READY)
        )

    metrics.auction_transitions_total.labels(to_status="ENDED").inc()
    events.emit(
        session,
        aggregate_type="auction",
        aggregate_id=auction.id,
        event_type=events.EventType.AUCTION_ENDED,
        payload={
            "auction": serializers.auction_state(auction),
            "outcome": auction.outcome.value,
            "winning_amount": (f"{auction.winning_amount:.2f}" if auction.winning_amount else None),
            "winner_id": str(auction.winner_id) if auction.winner_id else None,
        },
    )
    log.info(
        "auction.closed",
        auction_id=str(auction.id),
        outcome=auction.outcome.value,
        price=f"{auction.current_price:.2f}",
        bids=auction.bid_count,
    )
    return auction


async def cancel_auction(
    session: AsyncSession, auction: Auction, *, reason: str, now: datetime | None = None
) -> Auction:
    now = now or datetime.now(timezone.utc)
    if auction.status in (AuctionStatus.ENDED, AuctionStatus.SETTLED, AuctionStatus.CANCELLED):
        raise ConflictError(f"Auction is already {auction.status.value}.")
    auction.status = AuctionStatus.CANCELLED
    auction.outcome = AuctionOutcome.CANCELLED
    auction.closed_at = now
    auction.notes = f"{auction.notes or ''}\n[cancelled] {reason}".strip()

    await session.execute(
        update(Bid)
        .where(Bid.auction_id == auction.id)
        .values(status=BidStatus.LOST, is_winning=False)
    )
    await _release_all_holds(session, auction, now=now)
    await session.execute(
        update(Bike).where(Bike.id == auction.bike_id).values(status=BikeStatus.READY)
    )
    metrics.auction_transitions_total.labels(to_status="CANCELLED").inc()
    events.emit(
        session,
        aggregate_type="auction",
        aggregate_id=auction.id,
        event_type=events.EventType.AUCTION_CANCELLED,
        payload={"auction": serializers.auction_state(auction), "reason": reason},
    )
    log.warning("auction.cancelled", auction_id=str(auction.id), reason=reason)
    return auction


async def _settle_holds(session: AsyncSession, auction: Auction, *, now: datetime) -> None:
    """Winner's deposit is captured against the sale; everyone else is refunded."""
    holds = (
        (
            await session.execute(
                select(DepositHold)
                .where(
                    DepositHold.auction_id == auction.id,
                    DepositHold.status == HoldStatus.ACTIVE,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for hold in holds:
        account = (
            await session.execute(
                select(DepositAccount)
                .where(DepositAccount.user_id == hold.user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if account is None:
            continue
        is_winner = auction.outcome == AuctionOutcome.SOLD and hold.user_id == auction.winner_id
        account.held = max(Decimal("0"), account.held - hold.amount)
        if is_winner:
            account.balance = max(Decimal("0"), account.balance - hold.amount)
            hold.status = HoldStatus.CAPTURED
            txn_type = DepositTxnType.CAPTURE
        else:
            hold.status = HoldStatus.RELEASED
            txn_type = DepositTxnType.RELEASE
        hold.resolved_at = now
        session.add(
            DepositTransaction(
                user_id=hold.user_id,
                type=txn_type,
                amount=hold.amount,
                auction_id=auction.id,
                reference=f"close:{auction.slug}",
                created_at=now,
            )
        )


async def _release_all_holds(session: AsyncSession, auction: Auction, *, now: datetime) -> None:
    holds = (
        (
            await session.execute(
                select(DepositHold)
                .where(
                    DepositHold.auction_id == auction.id,
                    DepositHold.status == HoldStatus.ACTIVE,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for hold in holds:
        account = (
            await session.execute(
                select(DepositAccount)
                .where(DepositAccount.user_id == hold.user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if account is not None:
            account.held = max(Decimal("0"), account.held - hold.amount)
        hold.status = HoldStatus.RELEASED
        hold.resolved_at = now
        session.add(
            DepositTransaction(
                user_id=hold.user_id,
                type=DepositTxnType.RELEASE,
                amount=hold.amount,
                auction_id=auction.id,
                reference=f"cancel:{auction.slug}",
                created_at=now,
            )
        )


async def _notify_participants(session: AsyncSession, auction: Auction, *, now: datetime) -> None:
    participants = (
        (
            await session.execute(
                select(Bid.bidder_id).where(Bid.auction_id == auction.id).group_by(Bid.bidder_id)
            )
        )
        .scalars()
        .all()
    )
    for user_id in participants:
        won = auction.outcome == AuctionOutcome.SOLD and user_id == auction.winner_id
        if won:
            n_type, title, body = (
                NotificationType.AUCTION_WON,
                "You won!",
                f"{auction.title} is yours at Rs {auction.current_price:,.0f}.",
            )
        elif auction.outcome == AuctionOutcome.RESERVE_NOT_MET:
            n_type, title, body = (
                NotificationType.RESERVE_NOT_MET,
                "Reserve not met",
                f"{auction.title} closed below its reserve price.",
            )
        else:
            n_type, title, body = (
                NotificationType.AUCTION_LOST,
                "Auction ended",
                f"{auction.title} closed at Rs {auction.current_price:,.0f}.",
            )
        session.add(
            Notification(
                user_id=user_id,
                type=n_type,
                title=title,
                body=body,
                data={"auction_id": str(auction.id), "slug": auction.slug},
                created_at=now,
            )
        )


async def refresh_live_gauge(session: AsyncSession) -> None:
    count = (
        await session.execute(
            select(func.count()).select_from(Auction).where(Auction.status == AuctionStatus.LIVE)
        )
    ).scalar_one()
    metrics.auctions_live.set(count)
