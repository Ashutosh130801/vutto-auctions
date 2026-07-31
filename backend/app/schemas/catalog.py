from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import BikeStatus, FuelType


class BikeCreate(BaseModel):
    registration_number: str = Field(min_length=4, max_length=20)
    make: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=96)
    variant: str | None = Field(default=None, max_length=96)
    year: int = Field(ge=1980, le=2100)
    engine_cc: int = Field(ge=25, le=3000)
    odometer_km: int = Field(ge=0, le=1_000_000)
    fuel_type: FuelType = FuelType.PETROL
    colour: str | None = Field(default=None, max_length=48)
    owners_count: int = Field(default=1, ge=1, le=20)
    city: str = Field(min_length=2, max_length=64)
    condition_grade: str = Field(pattern="^[ABCD]$")
    inspection_score: int = Field(ge=0, le=100)
    inspection: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=20)
    description: str | None = Field(default=None, max_length=4000)
    estimated_value: Decimal = Field(gt=0, le=Decimal("100000000"))

    @field_validator("registration_number")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper().replace(" ", "")

    @field_validator("images")
    @classmethod
    def _safe_urls(cls, v: list[str]) -> list[str]:
        for url in v:
            if not url.startswith(("https://", "http://", "/")):
                raise ValueError("Image URLs must be absolute http(s) or root-relative.")
        return v


class BikeUpdate(BaseModel):
    odometer_km: int | None = Field(default=None, ge=0)
    condition_grade: str | None = Field(default=None, pattern="^[ABCD]$")
    inspection_score: int | None = Field(default=None, ge=0, le=100)
    inspection: dict[str, Any] | None = None
    images: list[str] | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=4000)
    estimated_value: Decimal | None = Field(default=None, gt=0)
    status: BikeStatus | None = None


class BikeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    registration_number: str
    make: str
    model: str
    variant: str | None
    year: int
    engine_cc: int
    odometer_km: int
    fuel_type: FuelType
    colour: str | None
    owners_count: int
    city: str
    condition_grade: str
    inspection_score: int
    inspection: dict[str, Any]
    images: list[str]
    description: str | None
    estimated_value: Decimal
    status: BikeStatus
    created_at: datetime
