"""Integration tests for the bidding engine against real PostgreSQL."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

import pytest
from app.core.errors import (
    AccountNotVerifiedError,
    AuctionEndedError,
    AuctionNotLiveError,
    BidTooLowError,
    InsufficientDepositError,
)
from app.db.models.auction import Auction
from app.db.models.bidding import Bid
from app.db.models.enums import (
    AuctionOutcome,
    AuctionStatus,
    BidSource,
    BidStatus,
    HoldStatus,
    UserStatus,
)
from app.db.models.finance import DepositAccount, DepositHold
from app.services import auction as auction_service
from app.services import bidding as bidding_service
from app.services.pricing import Verdict
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _bid(sessionmaker_, auction_id, user, amount, **kwargs):
    async with sessionmaker_() as s:
        fresh = await s.get(type(user), user.id)
        result = await bidding_service.place_bid(
            s, auction_id=auction_id, bidder=fresh, max_amount=D(amount), **kwargs
        )
        await s.commit()
        return result


async def test_first_bid_sets_price_to_start_price(sessionmaker_, make_user, make_auction):
    auction = await make_auction(start_price=D("50000"), increment=D("1000"))
    user = await make_user()

    result = await _bid(sessionmaker_, auction.id, user, "80000")

    assert result.verdict is Verdict.LEAD_TAKEN
    assert result.is_leading
    assert result.current_price == D("50000.00")
    assert result.minimum_next_bid == D("51000.00")


async def test_second_higher_bidder_takes_the_lead(sessionmaker_, make_user, make_auction):
    auction = await make_auction()
    a, b = await make_user(), await make_user()

    await _bid(sessionmaker_, auction.id, a, "60000")
    result = await _bid(sessionmaker_, auction.id, b, "75000")

    assert result.is_leading
    assert result.current_price == D("61000.00")  # a's max + one increment


async def test_lower_challenger_is_outbid_and_engine_records_the_defence(
    sessionmaker_, make_user, make_auction
):
    """The defensive proxy bid must appear in the ledger, or the price jump
    would be unexplained."""
    auction = await make_auction()
    a, b = await make_user(), await make_user()

    await _bid(sessionmaker_, auction.id, a, "90000")
    result = await _bid(sessionmaker_, auction.id, b, "70000")

    assert result.verdict is Verdict.OUTBID_IMMEDIATELY
    assert not result.is_leading
    assert result.current_price == D("71000.00")
    assert len(result.created_bids) == 2

    async with sessionmaker_() as s:
        bids = (
            (
                await s.execute(
                    select(Bid).where(Bid.auction_id == auction.id).order_by(Bid.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert [b.source for b in bids] == [BidSource.MANUAL, BidSource.MANUAL, BidSource.PROXY]
        assert bids[-1].bidder_id == a.id
        assert bids[-1].status is BidStatus.LEADING


async def test_bid_below_minimum_is_rejected(sessionmaker_, make_user, make_auction):
    auction = await make_auction()
    a, b = await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")

    with pytest.raises(BidTooLowError) as exc:
        await _bid(sessionmaker_, auction.id, b, "50500")
    assert exc.value.details["minimum_required"] == "51000.00"


async def test_leader_can_raise_their_ceiling_without_moving_the_price(
    sessionmaker_, make_user, make_auction
):
    auction = await make_auction()
    a = await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")

    result = await _bid(sessionmaker_, auction.id, a, "90000")

    assert result.verdict is Verdict.LEAD_RAISED
    assert result.current_price == D("50000.00")
    assert result.is_leading


async def test_unverified_account_cannot_bid(sessionmaker_, make_user, make_auction):
    auction = await make_auction()
    user = await make_user()
    async with sessionmaker_() as s:
        fresh = await s.get(type(user), user.id)
        fresh.kyc_verified = False
        fresh.status = UserStatus.PENDING
        await s.commit()

    with pytest.raises(AccountNotVerifiedError):
        await _bid(sessionmaker_, auction.id, user, "60000")


async def test_scheduled_auction_rejects_bids(sessionmaker_, make_user, make_auction):
    auction = await make_auction(
        starts_in=timedelta(hours=1), ends_in=timedelta(hours=5), live=False
    )
    user = await make_user()
    with pytest.raises(AuctionNotLiveError):
        await _bid(sessionmaker_, auction.id, user, "60000")


async def test_bid_after_end_time_is_rejected_even_before_the_sweeper_runs(
    sessionmaker_, make_user, make_auction
):
    """The clock is authoritative, not the status column."""
    auction = await make_auction(starts_in=timedelta(hours=-2), ends_in=timedelta(seconds=-1))
    user = await make_user()
    with pytest.raises(AuctionEndedError):
        await _bid(sessionmaker_, auction.id, user, "60000")


# ----------------------------------------------------------------- deposits
async def test_insufficient_deposit_blocks_bidding(sessionmaker_, make_user, make_auction):
    auction = await make_auction(deposit_required=D("25000"))
    user = await make_user(deposit=D("1000"))
    with pytest.raises(InsufficientDepositError) as exc:
        await _bid(sessionmaker_, auction.id, user, "60000")
    assert exc.value.details["shortfall"] == "24000.00"


async def test_hold_follows_the_lead_and_is_released_on_outbid(
    sessionmaker_, make_user, make_auction
):
    auction = await make_auction(deposit_required=D("5000"))
    a, b = await make_user(), await make_user()

    await _bid(sessionmaker_, auction.id, a, "60000")
    async with sessionmaker_() as s:
        acct = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == a.id))
        ).scalar_one()
        assert acct.held == D("5000.00")

    await _bid(sessionmaker_, auction.id, b, "80000")
    async with sessionmaker_() as s:
        a_acct = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == a.id))
        ).scalar_one()
        b_acct = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == b.id))
        ).scalar_one()
        assert a_acct.held == D("0.00"), "outbid bidder's deposit must be freed"
        assert b_acct.held == D("5000.00")


# --------------------------------------------------------------- anti-snipe
async def test_bid_in_the_final_window_extends_the_auction(sessionmaker_, make_user, make_auction):
    auction = await make_auction(
        ends_in=timedelta(seconds=30),
        anti_snipe_window_seconds=60,
        anti_snipe_extension_seconds=120,
    )
    original_end = auction.ends_at
    user = await make_user()

    result = await _bid(sessionmaker_, auction.id, user, "60000")

    assert result.extended
    assert result.new_ends_at == original_end + timedelta(seconds=120)


async def test_extension_is_capped(sessionmaker_, make_user, make_auction):
    auction = await make_auction(
        ends_in=timedelta(seconds=30),
        anti_snipe_window_seconds=3600,  # every bid is "in the window"
        anti_snipe_extension_seconds=1,
        anti_snipe_max_extensions=2,
    )
    a, b = await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")
    await _bid(sessionmaker_, auction.id, b, "70000")
    result = await _bid(sessionmaker_, auction.id, a, "90000")

    assert not result.extended, "extensions must stop at the cap"
    async with sessionmaker_() as s:
        fresh = await s.get(Auction, auction.id)
        assert fresh.extension_count == 2


async def test_bid_outside_the_window_does_not_extend(sessionmaker_, make_user, make_auction):
    auction = await make_auction(ends_in=timedelta(hours=3), anti_snipe_window_seconds=120)
    user = await make_user()
    result = await _bid(sessionmaker_, auction.id, user, "60000")
    assert not result.extended


# ------------------------------------------------------------------ closing
async def test_close_marks_winner_and_captures_their_deposit(
    sessionmaker_, make_user, make_auction
):
    auction = await make_auction(deposit_required=D("5000"))
    a, b = await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")
    await _bid(sessionmaker_, auction.id, b, "90000")

    async with sessionmaker_() as s:
        fresh = (
            await s.execute(select(Auction).where(Auction.id == auction.id).with_for_update())
        ).scalar_one()
        await auction_service.close_auction(s, fresh)
        await s.commit()

    async with sessionmaker_() as s:
        fresh = await s.get(Auction, auction.id)
        assert fresh.status is AuctionStatus.ENDED
        assert fresh.outcome is AuctionOutcome.SOLD
        assert fresh.winner_id == b.id
        assert fresh.winning_amount == D("61000.00")

        winner = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == b.id))
        ).scalar_one()
        assert winner.held == D("0.00")
        assert winner.balance == D("95000.00"), "winner's deposit is captured"

        loser = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == a.id))
        ).scalar_one()
        assert loser.balance == D("100000.00"), "loser is refunded in full"

        won = (
            (await s.execute(select(Bid).where(Bid.auction_id == auction.id, Bid.is_winning)))
            .scalars()
            .all()
        )
        assert len(won) == 1 and won[0].bidder_id == b.id


async def test_reserve_not_met_means_no_sale(sessionmaker_, make_user, make_auction):
    auction = await make_auction(reserve_price=D("200000"), deposit_required=D("5000"))
    a = await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")

    async with sessionmaker_() as s:
        fresh = (
            await s.execute(select(Auction).where(Auction.id == auction.id).with_for_update())
        ).scalar_one()
        await auction_service.close_auction(s, fresh)
        await s.commit()

    async with sessionmaker_() as s:
        fresh = await s.get(Auction, auction.id)
        assert fresh.outcome is AuctionOutcome.RESERVE_NOT_MET
        assert fresh.winner_id is None
        acct = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == a.id))
        ).scalar_one()
        assert acct.balance == D("100000.00"), "no sale means a full refund"


async def test_auction_with_no_bids_closes_as_no_bids(sessionmaker_, make_auction):
    auction = await make_auction()
    async with sessionmaker_() as s:
        fresh = (
            await s.execute(select(Auction).where(Auction.id == auction.id).with_for_update())
        ).scalar_one()
        await auction_service.close_auction(s, fresh)
        await s.commit()
    async with sessionmaker_() as s:
        assert (await s.get(Auction, auction.id)).outcome is AuctionOutcome.NO_BIDS


async def test_cancelling_releases_every_hold(sessionmaker_, make_user, make_auction):
    auction = await make_auction(deposit_required=D("5000"))
    a = await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")

    async with sessionmaker_() as s:
        fresh = (
            await s.execute(select(Auction).where(Auction.id == auction.id).with_for_update())
        ).scalar_one()
        await auction_service.cancel_auction(s, fresh, reason="Title dispute")
        await s.commit()

    async with sessionmaker_() as s:
        acct = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == a.id))
        ).scalar_one()
        assert acct.held == D("0.00")
        holds = (
            (await s.execute(select(DepositHold).where(DepositHold.auction_id == auction.id)))
            .scalars()
            .all()
        )
        assert all(h.status is HoldStatus.RELEASED for h in holds)


async def test_scheduler_closes_due_auctions(sessionmaker_, make_user, make_auction):
    auction = await make_auction(starts_in=timedelta(hours=-2), ends_in=timedelta(seconds=-1))
    async with sessionmaker_() as s:
        closed = await auction_service.close_due_auctions(s)
        await s.commit()
    assert closed == 1
    async with sessionmaker_() as s:
        assert (await s.get(Auction, auction.id)).status is AuctionStatus.ENDED


# ------------------------------------------------------------------- ledger
async def test_ledger_verifies_after_a_normal_bidding_session(
    sessionmaker_, make_user, make_auction
):
    auction = await make_auction()
    a, b, c = await make_user(), await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")
    await _bid(sessionmaker_, auction.id, b, "70000")
    await _bid(sessionmaker_, auction.id, c, "65000")
    await _bid(sessionmaker_, auction.id, a, "150000")

    async with sessionmaker_() as s:
        verdict = await bidding_service.verify_ledger(s, auction.id)
    assert verdict.valid
    assert verdict.entries_checked >= 4


async def test_tampering_with_a_bid_amount_breaks_the_chain(sessionmaker_, make_user, make_auction):
    """The whole point of the hash chain — a doctored history must be detectable."""
    auction = await make_auction()
    a, b = await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")
    await _bid(sessionmaker_, auction.id, b, "90000")

    async with sessionmaker_() as s:
        first = (
            await s.execute(select(Bid).where(Bid.auction_id == auction.id, Bid.sequence == 1))
        ).scalar_one()
        first.amount = D("1.00")  # a hostile DBA rewrites history
        await s.commit()

    async with sessionmaker_() as s:
        verdict = await bidding_service.verify_ledger(s, auction.id)
    assert not verdict.valid
    assert verdict.broken_at_sequence == 1
    assert verdict.reason == "entry contents were altered"


async def test_deleting_a_bid_breaks_the_chain(sessionmaker_, make_user, make_auction):
    auction = await make_auction()
    a, b, c = await make_user(), await make_user(), await make_user()
    await _bid(sessionmaker_, auction.id, a, "60000")
    await _bid(sessionmaker_, auction.id, b, "90000")
    await _bid(sessionmaker_, auction.id, c, "120000")

    async with sessionmaker_() as s:
        victim = (
            await s.execute(select(Bid).where(Bid.auction_id == auction.id, Bid.sequence == 2))
        ).scalar_one()
        await s.delete(victim)
        await s.commit()

    async with sessionmaker_() as s:
        verdict = await bidding_service.verify_ledger(s, auction.id)
    assert not verdict.valid


async def test_last_ledger_entry_always_equals_the_live_price(
    sessionmaker_, make_user, make_auction
):
    """Invariant: every price the auction ever showed is explained by a bid."""
    auction = await make_auction()
    users = [await make_user() for _ in range(4)]
    for i, u in enumerate(users):
        await _bid(sessionmaker_, auction.id, u, str(60000 + i * 7000))

    async with sessionmaker_() as s:
        fresh = await s.get(Auction, auction.id)
        last = (
            await s.execute(
                select(Bid)
                .where(Bid.auction_id == auction.id)
                .order_by(Bid.sequence.desc())
                .limit(1)
            )
        ).scalar_one()
        assert last.amount == fresh.current_price
        assert last.id == fresh.leading_bid_id
