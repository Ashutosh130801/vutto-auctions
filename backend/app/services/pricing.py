"""Pure proxy-bidding arithmetic.

Deliberately free of I/O so the rules can be unit-tested exhaustively and read
by a non-engineer.  The engine in ``services/bidding.py`` is the only caller.

Model (eBay-style proxy / automatic bidding)
--------------------------------------------
Every bidder submits a **maximum** they are willing to pay.  The platform bids
on their behalf in ``increment`` steps, only as high as needed to lead.  So the
*displayed* price is a function of the top **two** maximums, not of the top one.

Cases, given an incumbent leader with max ``L`` and a challenger with max ``C``:

* ``C >  L``  → challenger leads at ``min(C, L + increment)``.
* ``C <= L``  → incumbent keeps the lead at ``min(L, C + increment)``.
  The challenger is outbid the instant they bid; this is correct and is why we
  return it as an explicit outcome rather than an error.

The ``min(...)`` clamps are what stop the price overshooting either party's
authorised maximum.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Verdict(str, Enum):
    LEAD_TAKEN = "LEAD_TAKEN"  # challenger is now the leader
    OUTBID_IMMEDIATELY = "OUTBID_IMMEDIATELY"  # incumbent held the lead
    LEAD_RAISED = "LEAD_RAISED"  # same user raised their own ceiling
    REJECTED_TOO_LOW = "REJECTED_TOO_LOW"


@dataclass(frozen=True, slots=True)
class BidOutcome:
    verdict: Verdict
    new_price: Decimal
    new_leader_max: Decimal
    leader_is_challenger: bool
    minimum_required: Decimal


def quantise(value: Decimal) -> Decimal:
    """Money is 2dp, half-up — the rounding a customer expects on an invoice."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def minimum_acceptable(
    *,
    has_bids: bool,
    start_price: Decimal,
    current_price: Decimal,
    increment: Decimal,
    challenger_is_leader: bool = False,
    leader_max: Decimal | None = None,
) -> Decimal:
    """The smallest ``max_amount`` that will be accepted from this bidder."""
    if not has_bids:
        return quantise(start_price)
    if challenger_is_leader and leader_max is not None:
        # Raising your own ceiling: must genuinely exceed what you already
        # authorised, otherwise it is a no-op that would confuse the UI.
        return quantise(leader_max + increment)
    return quantise(current_price + increment)


def resolve(
    *,
    challenger_max: Decimal,
    leader_max: Decimal | None,
    current_price: Decimal,
    start_price: Decimal,
    increment: Decimal,
    has_bids: bool,
    challenger_is_leader: bool = False,
) -> BidOutcome:
    challenger_max = quantise(challenger_max)
    increment = quantise(increment)
    current_price = quantise(current_price)
    start_price = quantise(start_price)

    minimum = minimum_acceptable(
        has_bids=has_bids,
        start_price=start_price,
        current_price=current_price,
        increment=increment,
        challenger_is_leader=challenger_is_leader,
        leader_max=leader_max,
    )
    if challenger_max < minimum:
        return BidOutcome(
            verdict=Verdict.REJECTED_TOO_LOW,
            new_price=current_price,
            new_leader_max=leader_max or Decimal("0"),
            leader_is_challenger=False,
            minimum_required=minimum,
        )

    # --- Opening bid ------------------------------------------------------
    if not has_bids or leader_max is None:
        # The first bidder pays exactly the start price regardless of how high
        # their ceiling is — there is nobody to bid against yet.
        return BidOutcome(
            verdict=Verdict.LEAD_TAKEN,
            new_price=quantise(start_price),
            new_leader_max=challenger_max,
            leader_is_challenger=True,
            minimum_required=minimum,
        )

    leader_max = quantise(leader_max)

    # --- Leader raising their own ceiling ---------------------------------
    if challenger_is_leader:
        # Price does not move: there is still no competing pressure.  The
        # bidder has simply given the engine more room to defend the lead.
        return BidOutcome(
            verdict=Verdict.LEAD_RAISED,
            new_price=current_price,
            new_leader_max=challenger_max,
            leader_is_challenger=True,
            minimum_required=minimum,
        )

    # --- Contested ---------------------------------------------------------
    if challenger_max > leader_max:
        new_price = min(challenger_max, quantise(leader_max + increment))
        return BidOutcome(
            verdict=Verdict.LEAD_TAKEN,
            new_price=quantise(max(new_price, current_price)),
            new_leader_max=challenger_max,
            leader_is_challenger=True,
            minimum_required=minimum,
        )

    # challenger_max <= leader_max → incumbent defends automatically.
    new_price = min(leader_max, quantise(challenger_max + increment))
    return BidOutcome(
        verdict=Verdict.OUTBID_IMMEDIATELY,
        new_price=quantise(max(new_price, current_price)),
        new_leader_max=leader_max,
        leader_is_challenger=False,
        minimum_required=minimum,
    )
