from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.catalog import Bike
from app.db.models.enums import AuctionOutcome, AuctionStatus


class Auction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single timed auction for one bike.

    Concurrency contract
    --------------------
    ``current_price``, ``leading_bid_id``, ``ends_at`` and ``bid_count`` are the
    *mutable hot fields*.  Every write to them happens inside a transaction that
    first takes ``SELECT ... FOR UPDATE`` on this row, which serialises all
    concurrent bidders on the same auction while leaving different auctions
    fully parallel.  ``version`` is additionally bumped on every accepted bid so
    clients can detect staleness and callers can assert on it in tests.
    """

    __tablename__ = "auctions"

    bike_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bikes.id", ondelete="RESTRICT"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[AuctionStatus] = mapped_column(
        SAEnum(AuctionStatus, name="auction_status"),
        default=AuctionStatus.SCHEDULED,
        nullable=False,
        index=True,
    )
    outcome: Mapped[AuctionOutcome] = mapped_column(
        SAEnum(AuctionOutcome, name="auction_outcome"),
        default=AuctionOutcome.PENDING,
        nullable=False,
    )

    # --- Schedule -----------------------------------------------------------
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Soft close (anti-sniping) -----------------------------------------
    anti_snipe_window_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    anti_snipe_extension_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    anti_snipe_max_extensions: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    extension_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Pricing ------------------------------------------------------------
    start_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reserve_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    bid_increment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    deposit_required: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )

    # --- Live state (guarded by the row lock) -------------------------------
    current_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    leading_bid_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    leading_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    bid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bidder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_bid_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Settlement ---------------------------------------------------------
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    winning_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    bike: Mapped[Bike] = relationship(back_populates="auctions", lazy="selectin")

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("start_price >= 0", name="start_price_non_negative"),
        CheckConstraint("bid_increment > 0", name="increment_positive"),
        CheckConstraint("current_price >= 0", name="current_price_non_negative"),
        CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= start_price", name="reserve_ge_start"
        ),
        # A bike can only sit in one non-terminal auction at a time.
        Index(
            "uq_bikes_one_open_auction",
            "bike_id",
            unique=True,
            postgresql_where=text("status IN ('SCHEDULED','LIVE')"),
        ),
        # The scheduler's hot query: "which live auctions are due to close?"
        Index("ix_auctions_status_ends_at", "status", "ends_at"),
        Index("ix_auctions_status_starts_at", "status", "starts_at"),
    )

    @property
    def reserve_met(self) -> bool:
        return self.reserve_price is None or self.current_price >= self.reserve_price

    @property
    def has_bids(self) -> bool:
        return self.bid_count > 0

    @property
    def minimum_next_bid(self) -> Decimal:
        """The lowest maximum a *new* bidder may submit."""
        if self.bid_count == 0:
            return self.start_price
        return self.current_price + self.bid_increment
