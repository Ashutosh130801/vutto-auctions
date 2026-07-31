from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import DepositTxnType, HoldStatus


class DepositAccount(TimestampMixin, Base):
    """Refundable security deposit — the real-world gate on frivolous bidding.

    ``available = balance - held``.  Becoming the leader on an auction places a
    hold; being outbid releases it; winning captures it against the invoice.
    Invariant ``held <= balance`` is enforced by the database, not just by code.
    """

    __tablename__ = "deposit_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    held: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)

    __table_args__ = (
        CheckConstraint("balance >= 0", name="balance_non_negative"),
        CheckConstraint("held >= 0", name="held_non_negative"),
        CheckConstraint("held <= balance", name="held_within_balance"),
    )

    @property
    def available(self) -> Decimal:
        return self.balance - self.held


class DepositHold(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "deposit_holds"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[HoldStatus] = mapped_column(
        SAEnum(HoldStatus, name="hold_status"), default=HoldStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # At most one live hold per (user, auction) — enforced in the DB so a
        # race can never double-hold a bidder's deposit.
        Index(
            "uq_deposit_holds_active",
            "user_id",
            "auction_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class DepositTransaction(UUIDPrimaryKeyMixin, Base):
    """Append-only money movement log; the account row is a materialised total."""

    __tablename__ = "deposit_transactions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[DepositTxnType] = mapped_column(
        SAEnum(DepositTxnType, name="deposit_txn_type"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    auction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auctions.id", ondelete="SET NULL")
    )
    reference: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_deposit_txn_user_created", "user_id", "created_at"),)
