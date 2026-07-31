from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models.enums import UserRole, UserStatus

_PASSWORD_RULES = (
    (re.compile(r"[a-z]"), "a lowercase letter"),
    (re.compile(r"[A-Z]"), "an uppercase letter"),
    (re.compile(r"\d"), "a digit"),
)


def validate_password(value: str) -> str:
    if len(value) < 10:
        raise ValueError("Password must be at least 10 characters long.")
    if len(value) > 128:
        raise ValueError("Password must be at most 128 characters long.")
    missing = [label for pattern, label in _PASSWORD_RULES if not pattern.search(value)]
    if missing:
        raise ValueError("Password must contain " + ", ".join(missing) + ".")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=24)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password(v)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: str | None = None
    role: UserRole
    status: UserStatus
    kyc_verified: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 - RFC 6750 scheme name
    expires_at: datetime
    user: UserOut
