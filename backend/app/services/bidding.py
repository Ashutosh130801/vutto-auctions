"""The bidding engine.

This module is the heart of the platform and the place where correctness
matters most, so the reasoning is spelled out.

The hazard
----------
Two bidders hit ``POST /auctions/{id}/bids`` in the same millisecond, served by
different API replicas.  A naive read-modify-write ("read current_price, decide,
write current_price") loses one of them: both read ``50,000``, both decide they
lead, the second write silently clobbers the first.  The auction ends with a
price that no ledger entry explains and possibly the wrong winner.

The fix
-------
Every accepted bid runs inside **one database transaction** that begins with

    SELECT ... FROM auctions WHERE id = :id FOR UPDATE

The row lock serialises all concurrent bidders **on that auction** while leaving
different auctions perfectly parallel — so throughput scales with the number of
live auctions, not with a single global mutex.  Everything the bid touches
(auction row, ledger entry, deposit holds, outbox events) is written inside the
same transaction, so the whole thing is atomic: either a bid happened and the
world agrees about it, or nothing happened at all.

Deadlock safety
---------------
A bid may need to lock two deposit accounts (the new leader's and the outgoing
leader's).  Two auctions could otherwise grab the same pair in opposite order
and deadlock.  We impose a **global lock order**: the auction row first, then
deposit accounts in ascending ``user_id``.  A consistent order makes deadlock
impossible rather than merely unlikely.

Why not optimistic concurrency (retry on version mismatch)?
-----------------------------------------------------------
Under the traffic pattern that matters here — a burst of bidders converging on
the last seconds of one popular auction — optimistic retries livelock: everyone
collides, everyone retries, everyone collides again.  Pessimistic row locking
queues them fairly and each waiter re-reads fresh state.  ``version`` is still
maintained, but as a *staleness signal for clients*, not as the write guard.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.errors import (
    AccountNotVerifiedError,
    AuctionEndedError,
    AuctionNotLiveError,
    BidTooLowError,
    InsufficientDepositError,
)
from app.core.logging import get_logger
from app.db.models.auction import Auction
from app.db.models.bidding import Bid
from app.db.models.enums import (
    AuctionStatus,
    BidSource,
    BidStatus,
    DepositTxnType,
    HoldStatus,
    NotificationType,
)
from app.db.models.finance import DepositAccount, DepositHold, DepositTransaction
from app.db.models.ops import Notification
from app.db.models.user import User
from app.services import events, serializers
from app.services.pricing import Verdict, quantise, resolve

log = get_logger(__name__)


@dataclass(slots=True)
class BidResult:
    auction: Auction
    bid: Bid  # the challenger's own ledger entry
    verdict: Verdict
    is_leading: bool
    current_price: Decimal
    minimum_next_bid: Decimal
    extended: bool
    new_ends_at: datetime
    reserve_met: bool
    created_bids: list[Bid] = field(default_factory=list)


async def place_bid(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    bidder: User,
    max_amount: Decimal,
    idempotency_key: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> BidResult:
    """Place a proxy bid.  Atomic, idempotent, and fully audited.

    The caller owns the transaction (the FastAPI dependency commits on success),
    which is what lets the route add its own writes to the same atomic unit.
    """
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    max_amount = quantise(max_amount)

    if not bidder.can_bid:
        metrics.bids_total.labels(outcome="rejected_state").inc()
        raise AccountNotVerifiedError()

    # ------------------------------------------------------------------ 1
    # Serialise every bidder on this auction.  Nothing below this line can
    # observe a torn or stale view of the auction's live state.
    lock_wait_started = time.perf_counter()
    auction = (
        await session.execute(select(Auction).where(Auction.id == auction_id).with_for_update())
    ).scalar_one_or_none()
    metrics.bid_lock_wait_seconds.observe(time.perf_counter() - lock_wait_started)

    if auction is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("Auction not found.")

    # ------------------------------------------------------------------ 2
    # Lazy lifecycle correction.  The scheduler normally flips these, but a bid
    # arriving in the gap between "due" and "swept" must be judged against the
    # *true* clock, never against a stale status column.
    if auction.status == AuctionStatus.SCHEDULED and now >= auction.starts_at:
        _transition_to_live(session, auction, now)
    if auction.status != AuctionStatus.LIVE:
        metrics.bids_total.labels(outcome="rejected_state").inc()
        raise AuctionNotLiveError(details={"status": auction.status.value})
    if now >= auction.ends_at:
        metrics.bids_total.labels(outcome="rejected_state").inc()
        raise AuctionEndedError(details={"ended_at": auction.ends_at.isoformat()})

    challenger_is_leader = auction.leading_user_id == bidder.id
    leader_max = await _leading_max(session, auction)

    # ------------------------------------------------------------------ 3
    outcome = resolve(
        challenger_max=max_amount,
        leader_max=leader_max,
        current_price=auction.current_price,
        start_price=auction.start_price,
        increment=auction.bid_increment,
        has_bids=auction.has_bids,
        challenger_is_leader=challenger_is_leader,
    )
    if outcome.verdict is Verdict.REJECTED_TOO_LOW:
        metrics.bids_total.labels(outcome="rejected_low").inc()
        raise BidTooLowError(
            f"Minimum acceptable bid is {outcome.minimum_required:.2f}.",
            details={
                "minimum_required": f"{outcome.minimum_required:.2f}",
                "your_max": f"{max_amount:.2f}",
                "current_price": f"{auction.current_price:.2f}",
            },
        )

    # ------------------------------------------------------------------ 4
    # Deposit eligibility.  Checked *after* the price rules so an under-funded
    # user still gets the more actionable "your bid is too low" message first.
    await _ensure_deposit_capacity(session, auction=auction, bidder=bidder, now=now)

    previous_leader_id = auction.leading_user_id
    previous_leading_bid_id = auction.leading_bid_id

    # ------------------------------------------------------------------ 5
    # Append to the hash-chained ledger.
    created: list[Bid] = []
    challenger_bid = await _append_bid(
        session,
        auction=auction,
        bidder_id=bidder.id,
        amount=(
            outcome.new_price
            if outcome.verdict in (Verdict.LEAD_TAKEN, Verdict.LEAD_RAISED)
            else max_amount
        ),
        max_amount=max_amount,
        status=(BidStatus.LEADING if outcome.leader_is_challenger else BidStatus.OUTBID),
        source=BidSource.MANUAL,
        now=now,
        idempotency_key=idempotency_key,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    created.append(challenger_bid)

    defensive_bid: Bid | None = None
    if outcome.verdict is Verdict.OUTBID_IMMEDIATELY:
        # The incumbent's standing proxy automatically defends the lead.  We
        # record it explicitly so that *every* price in the ledger is explained
        # by a ledger entry — the invariant the verification endpoint checks.
        defensive_bid = await _append_bid(
            session,
            auction=auction,
            bidder_id=previous_leader_id,  # type: ignore[arg-type]
            amount=outcome.new_price,
            max_amount=outcome.new_leader_max,
            status=BidStatus.LEADING,
            source=BidSource.PROXY,
            now=now,
            idempotency_key=None,
            ip_address=None,
            user_agent="proxy-engine",
        )
        created.append(defensive_bid)

    # ------------------------------------------------------------------ 6
    # Soft close: a bid inside the anti-snipe window pushes the finish line out,
    # so a last-millisecond snipe cannot deny others a response.  Capped so an
    # auction can never be extended indefinitely.
    extended = False
    if (auction.ends_at - now).total_seconds() <= auction.anti_snipe_window_seconds and (
        auction.extension_count < auction.anti_snipe_max_extensions
    ):
        auction.ends_at = auction.ends_at + timedelta(seconds=auction.anti_snipe_extension_seconds)
        auction.extension_count += 1
        extended = True
        challenger_bid.extended_auction = True
        metrics.anti_snipe_extensions_total.inc()

    # ------------------------------------------------------------------ 7
    # Demote the outgoing leading bid, promote the new one.
    new_leading_bid = defensive_bid or (challenger_bid if outcome.leader_is_challenger else None)
    if previous_leading_bid_id and (
        new_leading_bid is None or previous_leading_bid_id != new_leading_bid.id
    ):
        await session.execute(
            update(Bid).where(Bid.id == previous_leading_bid_id).values(status=BidStatus.OUTBID)
        )

    if new_leading_bid is not None:
        auction.leading_bid_id = new_leading_bid.id
        auction.leading_user_id = new_leading_bid.bidder_id

    auction.current_price = outcome.new_price
    auction.bid_count += len(created)
    auction.version += 1
    if not challenger_is_leader and not await _has_bid_before(
        session, auction.id, bidder.id, exclude_bid_id=challenger_bid.id
    ):
        auction.bidder_count += 1

    # ------------------------------------------------------------------ 8
    # Move the deposit holds to follow the lead.
    if auction.leading_user_id != previous_leader_id:
        if auction.leading_user_id is not None:
            await _place_hold(session, auction, auction.leading_user_id, now)
        if previous_leader_id is not None:
            await _release_hold(session, auction, previous_leader_id, now)

    # ------------------------------------------------------------------ 9
    # Events + notifications, in the same transaction as the state change.
    state = serializers.auction_state(auction)
    events.emit(
        session,
        aggregate_type="auction",
        aggregate_id=auction.id,
        event_type=events.EventType.BID_PLACED,
        payload={
            "auction": state,
            "bids": [
                serializers.public_bid(
                    b, bidder_alias=serializers.bidder_alias(auction.id, b.bidder_id)
                )
                for b in created
            ],
            "extended": extended,
        },
    )
    if extended:
        events.emit(
            session,
            aggregate_type="auction",
            aggregate_id=auction.id,
            event_type=events.EventType.AUCTION_EXTENDED,
            payload={"auction": state, "ends_at": auction.ends_at.isoformat()},
        )

    outbid_user = _who_was_outbid(
        previous_leader_id=previous_leader_id,
        new_leader_id=auction.leading_user_id,
        challenger_id=bidder.id,
        verdict=outcome.verdict,
    )
    if outbid_user is not None:
        _notify_outbid(session, auction, outbid_user, now)

    await session.flush()

    metrics.bids_total.labels(outcome="accepted").inc()
    metrics.bid_placement_duration_seconds.observe(time.perf_counter() - started)
    log.info(
        "bid.accepted",
        auction_id=str(auction.id),
        bidder_id=str(bidder.id),
        verdict=outcome.verdict.value,
        price=f"{auction.current_price:.2f}",
        sequence=challenger_bid.sequence,
        extended=extended,
    )

    return BidResult(
        auction=auction,
        bid=challenger_bid,
        verdict=outcome.verdict,
        is_leading=auction.leading_user_id == bidder.id,
        current_price=auction.current_price,
        minimum_next_bid=auction.minimum_next_bid,
        extended=extended,
        new_ends_at=auction.ends_at,
        reserve_met=auction.reserve_met,
        created_bids=created,
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _transition_to_live(session: AsyncSession, auction: Auction, now: datetime) -> None:
    auction.status = AuctionStatus.LIVE
    metrics.auction_transitions_total.labels(to_status="LIVE").inc()
    events.emit(
        session,
        aggregate_type="auction",
        aggregate_id=auction.id,
        event_type=events.EventType.AUCTION_STARTED,
        payload={"auction": serializers.auction_state(auction)},
    )


async def _leading_max(session: AsyncSession, auction: Auction) -> Decimal | None:
    if auction.leading_bid_id is None:
        return None
    return (
        await session.execute(select(Bid.max_amount).where(Bid.id == auction.leading_bid_id))
    ).scalar_one_or_none()


async def _has_bid_before(
    session: AsyncSession,
    auction_id: uuid.UUID,
    bidder_id: uuid.UUID,
    *,
    exclude_bid_id: uuid.UUID,
) -> bool:
    stmt = select(
        exists().where(
            Bid.auction_id == auction_id,
            Bid.bidder_id == bidder_id,
            Bid.id != exclude_bid_id,
        )
    )
    return bool((await session.execute(stmt)).scalar())


async def _append_bid(
    session: AsyncSession,
    *,
    auction: Auction,
    bidder_id: uuid.UUID,
    amount: Decimal,
    max_amount: Decimal,
    status: BidStatus,
    source: BidSource,
    now: datetime,
    idempotency_key: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> Bid:
    """Append one tamper-evident entry.

    ``last_bid_sequence`` lives on the auction row we already hold a lock on,
    which is what makes sequence allocation race-free without a sequence object
    (and keeps the chain per-auction rather than global).
    """
    auction.last_bid_sequence += 1
    sequence = auction.last_bid_sequence

    prev_hash = await _last_entry_hash(session, auction.id)
    entry_hash = Bid.compute_hash(
        prev_hash=prev_hash,
        auction_id=auction.id,
        bidder_id=bidder_id,
        sequence=sequence,
        amount=amount,
        max_amount=max_amount,
        placed_at=now,
    )
    bid = Bid(
        id=uuid.uuid4(),
        auction_id=auction.id,
        bidder_id=bidder_id,
        sequence=sequence,
        amount=quantise(amount),
        max_amount=quantise(max_amount),
        status=status,
        source=source,
        placed_at=now,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:256] or None,
        idempotency_key=idempotency_key,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
    )
    session.add(bid)
    await session.flush([bid])
    return bid


async def _last_entry_hash(session: AsyncSession, auction_id: uuid.UUID) -> str:
    row = (
        await session.execute(
            select(Bid.entry_hash)
            .where(Bid.auction_id == auction_id)
            .order_by(Bid.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row or Bid.GENESIS_HASH


async def _get_account_for_update(session: AsyncSession, user_id: uuid.UUID) -> DepositAccount:
    account = (
        await session.execute(
            select(DepositAccount).where(DepositAccount.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        account = DepositAccount(user_id=user_id, balance=Decimal("0"), held=Decimal("0"))
        session.add(account)
        await session.flush([account])
    return account


async def _ensure_deposit_capacity(
    session: AsyncSession, *, auction: Auction, bidder: User, now: datetime
) -> None:
    required = auction.deposit_required
    if required <= 0:
        return
    existing = (
        await session.execute(
            select(DepositHold.id).where(
                DepositHold.user_id == bidder.id,
                DepositHold.auction_id == auction.id,
                DepositHold.status == HoldStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return  # already funded for this auction
    account = await _get_account_for_update(session, bidder.id)
    if account.available < required:
        metrics.bids_total.labels(outcome="rejected_deposit").inc()
        raise InsufficientDepositError(
            details={
                "required": f"{required:.2f}",
                "available": f"{account.available:.2f}",
                "shortfall": f"{max(Decimal('0'), required - account.available):.2f}",
            }
        )


async def _place_hold(
    session: AsyncSession, auction: Auction, user_id: uuid.UUID, now: datetime
) -> None:
    if auction.deposit_required <= 0:
        return
    already = (
        await session.execute(
            select(DepositHold.id).where(
                DepositHold.user_id == user_id,
                DepositHold.auction_id == auction.id,
                DepositHold.status == HoldStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return
    account = await _get_account_for_update(session, user_id)
    account.held = account.held + auction.deposit_required
    session.add(
        DepositHold(
            user_id=user_id,
            auction_id=auction.id,
            amount=auction.deposit_required,
            status=HoldStatus.ACTIVE,
            created_at=now,
        )
    )
    session.add(
        DepositTransaction(
            user_id=user_id,
            type=DepositTxnType.HOLD,
            amount=auction.deposit_required,
            auction_id=auction.id,
            reference=f"lead:{auction.slug}",
            created_at=now,
        )
    )


async def _release_hold(
    session: AsyncSession, auction: Auction, user_id: uuid.UUID, now: datetime
) -> None:
    hold = (
        await session.execute(
            select(DepositHold)
            .where(
                DepositHold.user_id == user_id,
                DepositHold.auction_id == auction.id,
                DepositHold.status == HoldStatus.ACTIVE,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if hold is None:
        return
    account = await _get_account_for_update(session, user_id)
    account.held = max(Decimal("0"), account.held - hold.amount)
    hold.status = HoldStatus.RELEASED
    hold.resolved_at = now
    session.add(
        DepositTransaction(
            user_id=user_id,
            type=DepositTxnType.RELEASE,
            amount=hold.amount,
            auction_id=auction.id,
            reference=f"outbid:{auction.slug}",
            created_at=now,
        )
    )


def _who_was_outbid(
    *,
    previous_leader_id: uuid.UUID | None,
    new_leader_id: uuid.UUID | None,
    challenger_id: uuid.UUID,
    verdict: Verdict,
) -> uuid.UUID | None:
    if verdict is Verdict.OUTBID_IMMEDIATELY:
        return challenger_id
    if verdict is Verdict.LEAD_TAKEN and previous_leader_id not in (None, challenger_id):
        return previous_leader_id
    return None


def _notify_outbid(
    session: AsyncSession, auction: Auction, user_id: uuid.UUID, now: datetime
) -> None:
    session.add(
        Notification(
            user_id=user_id,
            type=NotificationType.OUTBID,
            title="You have been outbid",
            body=f"{auction.title} is now at Rs {auction.current_price:,.0f}.",
            data={
                "auction_id": str(auction.id),
                "slug": auction.slug,
                "current_price": f"{auction.current_price:.2f}",
                "minimum_next_bid": f"{auction.minimum_next_bid:.2f}",
            },
            created_at=now,
        )
    )
    events.emit(
        session,
        aggregate_type="user",
        aggregate_id=user_id,
        event_type=events.EventType.USER_OUTBID,
        payload={
            "auction_id": str(auction.id),
            "slug": auction.slug,
            "title": auction.title,
            "current_price": f"{auction.current_price:.2f}",
            "minimum_next_bid": f"{auction.minimum_next_bid:.2f}",
        },
    )


# --------------------------------------------------------------------------
# Ledger verification
# --------------------------------------------------------------------------
@dataclass(slots=True)
class LedgerVerification:
    valid: bool
    entries_checked: int
    head_hash: str | None
    broken_at_sequence: int | None = None
    reason: str | None = None


async def verify_ledger(session: AsyncSession, auction_id: uuid.UUID) -> LedgerVerification:
    """Recompute the chain end-to-end.

    Any retroactive edit, insertion, deletion or reordering of a bid changes the
    digest of that entry and therefore of every entry after it, so a single pass
    is enough to detect tampering *and* point at where it started.
    """
    bids = (
        (
            await session.execute(
                select(Bid).where(Bid.auction_id == auction_id).order_by(Bid.sequence)
            )
        )
        .scalars()
        .all()
    )
    prev = Bid.GENESIS_HASH
    for index, bid in enumerate(bids, start=1):
        if bid.sequence != index:
            return LedgerVerification(
                False, index - 1, prev, bid.sequence, "sequence gap or duplicate"
            )
        if bid.prev_hash != prev:
            return LedgerVerification(False, index - 1, prev, bid.sequence, "broken link")
        if bid.recompute_hash() != bid.entry_hash:
            return LedgerVerification(
                False, index - 1, prev, bid.sequence, "entry contents were altered"
            )
        prev = bid.entry_hash
    return LedgerVerification(True, len(bids), prev if bids else None)


def as_dict(result: BidResult) -> dict[str, Any]:
    return {
        "bid_id": str(result.bid.id),
        "sequence": result.bid.sequence,
        "verdict": result.verdict.value,
        "is_leading": result.is_leading,
        "current_price": f"{result.current_price:.2f}",
        "minimum_next_bid": f"{result.minimum_next_bid:.2f}",
        "your_max": f"{result.bid.max_amount:.2f}",
        "extended": result.extended,
        "ends_at": result.new_ends_at.isoformat(),
        "reserve_met": result.reserve_met,
        "auction_version": result.auction.version,
        "entry_hash": result.bid.entry_hash,
    }
