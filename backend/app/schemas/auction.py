from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.enums import AuctionOutcome, AuctionStatus, BidSource, BidStatus
from app.schemas.catalog import BikeOut


class AuctionCreate(BaseModel):
    bike_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    start_price: Decimal = Field(ge=0, le=Decimal("100000000"))
    bid_increment: Decimal = Field(gt=0, le=Decimal("1000000"))
    reserve_price: Decimal | None = Field(default=None, ge=0)
    deposit_required: Decimal = Field(default=Decimal("0"), ge=0)
    anti_snipe_window_seconds: int = Field(default=120, ge=0, le=3600)
    anti_snipe_extension_seconds: int = Field(default=120, ge=0, le=3600)
    anti_snipe_max_extensions: int = Field(default=20, ge=0, le=500)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _coherent(self) -> AuctionCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at.")
        if (self.ends_at - self.starts_at).total_seconds() < 60:
            raise ValueError("An auction must run for at least 60 seconds.")
        if self.reserve_price is not None and self.reserve_price < self.start_price:
            raise ValueError("reserve_price cannot be below start_price.")
        return self


class AuctionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    status: AuctionStatus
    outcome: AuctionOutcome
    starts_at: datetime
    ends_at: datetime
    current_price: Decimal
    start_price: Decimal
    bid_increment: Decimal
    deposit_required: Decimal
    bid_count: int
    bidder_count: int
    version: int
    has_reserve: bool = False
    reserve_met: bool = True
    minimum_next_bid: Decimal = Decimal("0")
    thumbnail: str | None = None
    city: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None


class AuctionDetail(AuctionSummary):
    notes: str | None = None
    scheduled_ends_at: datetime
    extension_count: int
    anti_snipe_window_seconds: int
    anti_snipe_extension_seconds: int
    anti_snipe_max_extensions: int
    closed_at: datetime | None = None
    winning_amount: Decimal | None = None
    bike: BikeOut
    # Populated only for the authenticated caller — never another bidder's.
    your_max_bid: Decimal | None = None
    you_are_leading: bool = False
    you_are_watching: bool = False


class BidCreate(BaseModel):
    """A bid is a *maximum*, not a price.

    The engine only ever spends as much of it as needed to lead, so submitting a
    high maximum is safe — this is the single most misunderstood part of proxy
    bidding and the UI says so explicitly.
    """

    max_amount: Decimal = Field(gt=0, le=Decimal("100000000"))
    expected_version: int | None = Field(
        default=None,
        description=(
            "Optimistic guard. If supplied and the auction has moved on, the bid "
            "is rejected so the client can re-render before the user commits."
        ),
    )


class BidOut(BaseModel):
    id: uuid.UUID
    sequence: int
    amount: Decimal
    status: BidStatus
    source: BidSource
    bidder_alias: str
    bidder_id: uuid.UUID
    placed_at: datetime
    entry_hash: str
    is_you: bool = False


class BidAccepted(BaseModel):
    bid_id: uuid.UUID
    sequence: int
    verdict: str
    is_leading: bool
    current_price: Decimal
    minimum_next_bid: Decimal
    your_max: Decimal
    extended: bool
    ends_at: datetime
    reserve_met: bool
    auction_version: int
    entry_hash: str


class LedgerVerdict(BaseModel):
    valid: bool
    entries_checked: int
    head_hash: str | None
    broken_at_sequence: int | None = None
    reason: str | None = None
