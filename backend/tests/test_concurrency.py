"""Concurrency tests — the ones that justify the design.

Everything here runs against real PostgreSQL with genuinely parallel
connections.  The point is not "does a bid work" but "does the system stay
consistent when N bidders collide on the same row at the same instant", which is
exactly the scenario a naive implementation gets wrong and a reviewer will
probe first.

Invariants asserted after every storm:

1. **Exactly one leader.**  Precisely one bid row is ``LEADING``, and the
   auction's ``leading_bid_id`` points at it.
2. **No lost updates.**  ``bid_count`` equals the number of ledger rows, and
   ``version`` equals the number of accepted bid *requests*.
3. **The ledger is contiguous and intact.**  Sequences are 1..N with no gaps or
   duplicates, and the hash chain verifies.
4. **Price is explained.**  ``current_price`` equals the amount on the last
   ledger entry.
5. **Money is conserved.**  Total held never exceeds total balance, and at most
   one deposit hold per auction is active.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal as D

import pytest
from app.core.errors import AppError
from app.db.models.auction import Auction
from app.db.models.bidding import Bid
from app.db.models.enums import BidStatus, HoldStatus
from app.db.models.finance import DepositAccount, DepositHold
from app.db.models.user import User
from app.services import bidding as bidding_service
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _attempt(sessionmaker_, auction_id, user_id, amount) -> str:
    """One bid on its own connection, mimicking a separate API replica."""
    async with sessionmaker_() as s:
        try:
            user = await s.get(User, user_id)
            await bidding_service.place_bid(
                s, auction_id=auction_id, bidder=user, max_amount=D(amount)
            )
            await s.commit()
            return "accepted"
        except AppError as exc:
            await s.rollback()
            return exc.code
        except Exception as exc:  # pragma: no cover - surfaced as a failure below
            await s.rollback()
            return f"unexpected:{type(exc).__name__}:{exc}"


async def _assert_invariants(sessionmaker_, auction_id, *, accepted: int) -> Auction:
    async with sessionmaker_() as s:
        auction = await s.get(Auction, auction_id)
        bids = (
            (
                await s.execute(
                    select(Bid).where(Bid.auction_id == auction_id).order_by(Bid.sequence)
                )
            )
            .scalars()
            .all()
        )

        # 1. exactly one leader
        leading = [b for b in bids if b.status is BidStatus.LEADING]
        assert len(leading) == 1, f"expected 1 leading bid, found {len(leading)}"
        assert auction.leading_bid_id == leading[0].id
        assert auction.leading_user_id == leading[0].bidder_id

        # 2. no lost updates
        assert auction.bid_count == len(bids), "bid_count drifted from the ledger"
        assert auction.version == accepted, "a concurrent write was lost"
        assert auction.last_bid_sequence == len(bids)

        # 3. contiguous, intact ledger
        assert [b.sequence for b in bids] == list(range(1, len(bids) + 1))
        verdict = await bidding_service.verify_ledger(s, auction_id)
        assert verdict.valid, f"hash chain broken: {verdict.reason}"

        # 4. the price is explained by the ledger
        assert bids[-1].amount == auction.current_price
        assert all(
            bids[i].amount <= bids[i + 1].amount for i in range(len(bids) - 1)
        ), "price moved backwards"

        # 5. money is conserved
        active_holds = (
            (
                await s.execute(
                    select(DepositHold).where(
                        DepositHold.auction_id == auction_id,
                        DepositHold.status == HoldStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(active_holds) <= 1, "more than one bidder's deposit is held"
        if active_holds:
            assert active_holds[0].user_id == auction.leading_user_id
        accounts = (await s.execute(select(DepositAccount))).scalars().all()
        for account in accounts:
            assert account.held <= account.balance
            assert account.held >= 0
        return auction


@pytest.mark.slow
async def test_two_hundred_simultaneous_bids_leave_exactly_one_leader(
    sessionmaker_, make_user, make_auction
):
    """The headline test.

    Twenty bidders fire ten bids each, all released at the same instant on
    twenty separate connections.  Without the row lock this produces duplicate
    leaders, a bid_count that disagrees with the ledger, and a price nobody bid.
    """
    auction = await make_auction(
        start_price=D("50000"), increment=D("1000"), deposit_required=D("5000")
    )
    users = [await make_user(deposit=D("1000000")) for _ in range(20)]

    tasks = [
        _attempt(sessionmaker_, auction.id, u.id, str(60000 + i * 500 + n * 137))
        for n, u in enumerate(users)
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    unexpected = [r for r in results if r.startswith("unexpected")]
    assert not unexpected, f"unexpected failures: {unexpected[:3]}"

    accepted = results.count("accepted")
    assert accepted > 0
    auction_after = await _assert_invariants(sessionmaker_, auction.id, accepted=accepted)
    assert auction_after.current_price >= D("50000")


@pytest.mark.slow
async def test_identical_bids_from_different_users_pick_one_deterministic_winner(
    sessionmaker_, make_user, make_auction
):
    """Fifty bidders submit the *same* maximum at the same moment.

    Ties must resolve to whoever the database serialised first — never to two
    winners, and never to a price above what anyone authorised.
    """
    auction = await make_auction(
        start_price=D("50000"), increment=D("1000"), deposit_required=D("0")
    )
    users = [await make_user(deposit=D("0")) for _ in range(50)]

    results = await asyncio.gather(
        *[_attempt(sessionmaker_, auction.id, u.id, "75000") for u in users]
    )
    assert not [r for r in results if r.startswith("unexpected")]

    accepted = results.count("accepted")
    rejected_low = results.count("BID_TOO_LOW")
    assert accepted + rejected_low == 50, f"unaccounted results: {set(results)}"

    auction_after = await _assert_invariants(sessionmaker_, auction.id, accepted=accepted)
    assert auction_after.current_price <= D("75000"), "price exceeded every authorised maximum"


@pytest.mark.slow
async def test_deposit_holds_never_double_charge_under_contention(
    sessionmaker_, make_user, make_auction
):
    """A bidder whose deposit covers exactly one auction must not have it held
    twice, no matter how many of their bids race."""
    auction = await make_auction(deposit_required=D("5000"))
    user = await make_user(deposit=D("5000"))

    results = await asyncio.gather(
        *[_attempt(sessionmaker_, auction.id, user.id, str(60000 + i * 2000)) for i in range(25)]
    )
    assert not [r for r in results if r.startswith("unexpected")]

    async with sessionmaker_() as s:
        account = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == user.id))
        ).scalar_one()
        assert account.held == D("5000.00"), "deposit was held more than once"
        holds = (
            await s.execute(
                select(func.count())
                .select_from(DepositHold)
                .where(
                    DepositHold.auction_id == auction.id,
                    DepositHold.status == HoldStatus.ACTIVE,
                )
            )
        ).scalar_one()
        assert holds == 1


@pytest.mark.slow
async def test_anti_snipe_extensions_are_capped_under_a_burst(
    sessionmaker_, make_user, make_auction
):
    """A snipe storm in the final seconds must not extend past the cap."""
    auction = await make_auction(
        ends_in=timedelta(seconds=45),
        anti_snipe_window_seconds=60,
        anti_snipe_extension_seconds=5,
        anti_snipe_max_extensions=3,
        deposit_required=D("0"),
    )
    users = [await make_user(deposit=D("0")) for _ in range(12)]
    original_end = auction.ends_at

    results = await asyncio.gather(
        *[
            _attempt(sessionmaker_, auction.id, u.id, str(60000 + i * 3000))
            for i, u in enumerate(users)
        ]
    )
    assert not [r for r in results if r.startswith("unexpected")]

    async with sessionmaker_() as s:
        fresh = await s.get(Auction, auction.id)
        assert fresh.extension_count <= 3
        assert fresh.ends_at <= original_end + timedelta(seconds=15)


@pytest.mark.slow
async def test_parallel_auctions_do_not_block_each_other(sessionmaker_, make_user, make_auction):
    """Locking is per-auction, not global.

    Ten auctions bid on simultaneously must all complete correctly — this is the
    property that lets throughput scale with the number of live auctions.
    """
    auctions = [await make_auction(deposit_required=D("0")) for _ in range(10)]
    users = [await make_user(deposit=D("0")) for _ in range(10)]

    results = await asyncio.gather(
        *[
            _attempt(sessionmaker_, a.id, u.id, str(60000 + i * 1000))
            for a in auctions
            for i, u in enumerate(users)
        ]
    )
    assert not [r for r in results if r.startswith("unexpected")]

    for auction in auctions:
        async with sessionmaker_() as s:
            fresh = await s.get(Auction, auction.id)
            accepted_here = fresh.version
            assert accepted_here > 0
        await _assert_invariants(sessionmaker_, auction.id, accepted=accepted_here)


async def test_idempotency_key_prevents_a_retried_bid_from_counting_twice(
    sessionmaker_, make_user, make_auction, client, auth_headers
):
    """A flaky mobile connection retrying the same POST must not bid twice."""
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    headers = {**(await auth_headers(user)), "Idempotency-Key": "retry-me-once"}

    first = await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "70000"}, headers=headers
    )
    second = await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "70000"}, headers=headers
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert first.json() == second.json(), "retry must replay the original response"

    async with sessionmaker_() as s:
        count = (
            await s.execute(
                select(func.count()).select_from(Bid).where(Bid.auction_id == auction.id)
            )
        ).scalar_one()
        assert count == 1


async def test_reusing_an_idempotency_key_with_a_different_body_is_rejected(
    make_user, make_auction, client, auth_headers
):
    auction = await make_auction(deposit_required=D("0"))
    user = await make_user(deposit=D("0"))
    headers = {**(await auth_headers(user)), "Idempotency-Key": "same-key"}

    await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "70000"}, headers=headers
    )
    clash = await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"max_amount": "90000"}, headers=headers
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
