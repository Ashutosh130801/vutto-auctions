from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import BikeStatus, FuelType

if TYPE_CHECKING:
    # Import for typing only: `auction.py` imports this module at runtime, so a
    # real import here would be circular.
    from app.db.models.auction import Auction


class Bike(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A physical motorcycle in inventory.

    ``inspection`` holds the 100-point report as JSONB: it is a document that
    evolves with the inspection checklist, and we never query inside it in a hot
    path, so a rigid relational model would buy us nothing but migrations.
    """

    __tablename__ = "bikes"

    registration_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    make: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    variant: Mapped[str | None] = mapped_column(String(96))
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    engine_cc: Mapped[int] = mapped_column(Integer, nullable=False)
    odometer_km: Mapped[int] = mapped_column(Integer, nullable=False)
    fuel_type: Mapped[FuelType] = mapped_column(
        SAEnum(FuelType, name="fuel_type"), default=FuelType.PETROL, nullable=False
    )
    colour: Mapped[str | None] = mapped_column(String(48))
    owners_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    condition_grade: Mapped[str] = mapped_column(String(2), nullable=False)  # A / B / C / D
    inspection_score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    inspection: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    images: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    estimated_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[BikeStatus] = mapped_column(
        SAEnum(BikeStatus, name="bike_status"), default=BikeStatus.DRAFT, nullable=False
    )
    seller_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    auctions: Mapped[list[Auction]] = relationship(back_populates="bike", lazy="raise")

    __table_args__ = (
        CheckConstraint("inspection_score BETWEEN 0 AND 100", name="inspection_score_range"),
        CheckConstraint("odometer_km >= 0", name="odometer_non_negative"),
        CheckConstraint("year BETWEEN 1980 AND 2100", name="year_sane"),
        CheckConstraint("condition_grade IN ('A','B','C','D')", name="condition_grade_valid"),
        Index("ix_bikes_search", "make", "model", "year", "city"),
    )

    @property
    def title(self) -> str:
        parts = [str(self.year), self.make, self.model]
        if self.variant:
            parts.append(self.variant)
        return " ".join(parts)
