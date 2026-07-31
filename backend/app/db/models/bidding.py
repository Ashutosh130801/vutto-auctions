from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, INETStr
from app.db.models.enums import BidSource, BidStatus


class Bid(Base):
    """An append-only, tamper-evident bid ledger.

    Two things make this more than a plain table:

    1. **Proxy semantics.**  ``max_amount`` is the ceiling the bidder authorised
       (never shown to anyone else); ``amount`` is the public price the engine
       actually set as a consequence.  This is eBay-style proxy bidding, which
       is what real vehicle auctions use.

    2. **Hash chain.**  Each row stores ``prev_hash`` (the hash of bid *n-1* in
       the same auction) and its own ``entry_hash``.  Recomputing the chain
       detects any retroactive edit, insertion or deletion — a bid history you
       can *prove* was not doctored.  ``GET /auctions/{id}/ledger`` exposes the
       verification.
    """

    __tablename__ = "bids"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    status: Mapped[BidStatus] = mapped_column(
        SAEnum(BidStatus, name="bid_status"), default=BidStatus.LEADING, nullable=False
    )
    source: Mapped[BidSource] = mapped_column(
        SAEnum(BidSource, name="bid_source"), default=BidSource.MANUAL, nullable=False
    )
    is_winning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extended_auction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INETStr)
    user_agent: Mapped[str | None] = mapped_column(String(256))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("auction_id", "sequence", name="uq_bids_auction_sequence"),
        UniqueConstraint("bidder_id", "idempotency_key", name="uq_bids_bidder_idempotency"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("max_amount >= amount", name="max_ge_amount"),
        Index("ix_bids_auction_seq_desc", "auction_id", "sequence"),
        Index("ix_bids_bidder_placed", "bidder_id", "placed_at"),
        # Supports the "is this a first-time bidder on this auction?" EXISTS
        # probe run on every bid, keeping bidder_count O(1) to maintain.
        Index("ix_bids_auction_bidder", "auction_id", "bidder_id"),
    )

    GENESIS_HASH = "0" * 64

    @staticmethod
    def compute_hash(
        *,
        prev_hash: str,
        auction_id: uuid.UUID,
        bidder_id: uuid.UUID,
        sequence: int,
        amount: Decimal,
        max_amount: Decimal,
        placed_at: datetime,
    ) -> str:
        """Deterministic digest over the fields that must never change.

        Amounts are normalised to 2dp strings and the timestamp to microsecond
        ISO-8601 so the digest is stable across drivers and round-trips.
        """
        payload = "|".join(
            [
                prev_hash,
                str(auction_id),
                str(bidder_id),
                str(sequence),
                f"{Decimal(amount):.2f}",
                f"{Decimal(max_amount):.2f}",
                placed_at.astimezone(tz=None).isoformat(timespec="microseconds")
                if placed_at.tzinfo is None
                else placed_at.isoformat(timespec="microseconds"),
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def recompute_hash(self) -> str:
        return self.compute_hash(
            prev_hash=self.prev_hash,
            auction_id=self.auction_id,
            bidder_id=self.bidder_id,
            sequence=self.sequence,
            amount=self.amount,
            max_amount=self.max_amount,
            placed_at=self.placed_at,
        )


class Watchlist(Base):
    __tablename__ = "watchlist"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
