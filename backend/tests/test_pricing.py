"""Unit tests for the proxy-bid rules.

Pure functions, no I/O — these run in milliseconds and pin down the behaviour
everything else depends on.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from app.services.pricing import Verdict, minimum_acceptable, quantise, resolve

INC = D("1000")
START = D("50000")


def test_first_bid_pays_start_price_not_their_maximum():
    """The single most important fairness property of proxy bidding."""
    outcome = resolve(
        challenger_max=D("999999"),
        leader_max=None,
        current_price=START,
        start_price=START,
        increment=INC,
        has_bids=False,
    )
    assert outcome.verdict is Verdict.LEAD_TAKEN
    assert outcome.new_price == START
    assert outcome.new_leader_max == D("999999")


def test_challenger_beats_leader_by_one_increment_only():
    outcome = resolve(
        challenger_max=D("120000"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.verdict is Verdict.LEAD_TAKEN
    assert outcome.new_price == D("101000")  # leader_max + one increment
    assert outcome.leader_is_challenger


def test_price_never_exceeds_challenger_maximum():
    """When the gap is smaller than an increment, the clamp must bind."""
    outcome = resolve(
        challenger_max=D("100500"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.new_price == D("100500")
    assert outcome.leader_is_challenger


def test_lower_maximum_is_outbid_immediately_and_pushes_price_up():
    outcome = resolve(
        challenger_max=D("90000"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.verdict is Verdict.OUTBID_IMMEDIATELY
    assert outcome.new_price == D("91000")
    assert not outcome.leader_is_challenger
    assert outcome.new_leader_max == D("100000")


def test_price_never_exceeds_incumbent_maximum_when_defending():
    outcome = resolve(
        challenger_max=D("99800"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.new_price == D("100000")  # clamped to leader's ceiling


def test_tie_on_maximum_favours_the_incumbent():
    """Whoever got there first keeps the lead — the standard auction-house rule."""
    outcome = resolve(
        challenger_max=D("100000"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.verdict is Verdict.OUTBID_IMMEDIATELY
    assert outcome.new_price == D("100000")


def test_bid_below_minimum_is_rejected():
    outcome = resolve(
        challenger_max=D("60500"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
    )
    assert outcome.verdict is Verdict.REJECTED_TOO_LOW
    assert outcome.minimum_required == D("61000")


def test_leader_raising_own_ceiling_does_not_move_the_price():
    outcome = resolve(
        challenger_max=D("150000"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
        challenger_is_leader=True,
    )
    assert outcome.verdict is Verdict.LEAD_RAISED
    assert outcome.new_price == D("60000")
    assert outcome.new_leader_max == D("150000")


def test_leader_must_exceed_their_own_maximum_to_raise():
    outcome = resolve(
        challenger_max=D("100500"),
        leader_max=D("100000"),
        current_price=D("60000"),
        start_price=START,
        increment=INC,
        has_bids=True,
        challenger_is_leader=True,
    )
    assert outcome.verdict is Verdict.REJECTED_TOO_LOW
    assert outcome.minimum_required == D("101000")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10.005", "10.01"), ("10.004", "10.00"), ("0.1", "0.10"), ("99999.999", "100000.00")],
)
def test_money_rounds_half_up_to_two_places(raw, expected):
    assert quantise(D(raw)) == D(expected)


def test_price_is_monotonic_across_a_random_bid_sequence():
    """Property check: no sequence of legal bids can ever lower the price."""
    import random

    rng = random.Random(7)
    price, leader_max, leader = START, None, None
    has_bids = False
    for bidder in (rng.choice(["a", "b", "c"]) for _ in range(400)):
        minimum = minimum_acceptable(
            has_bids=has_bids,
            start_price=START,
            current_price=price,
            increment=INC,
            challenger_is_leader=bidder == leader,
            leader_max=leader_max,
        )
        challenger_max = minimum + INC * rng.randint(0, 5)
        outcome = resolve(
            challenger_max=challenger_max,
            leader_max=leader_max,
            current_price=price,
            start_price=START,
            increment=INC,
            has_bids=has_bids,
            challenger_is_leader=bidder == leader,
        )
        assert outcome.verdict is not Verdict.REJECTED_TOO_LOW
        assert outcome.new_price >= price, "price went backwards"
        price = outcome.new_price
        leader_max = outcome.new_leader_max
        if outcome.leader_is_challenger:
            leader = bidder
        has_bids = True
