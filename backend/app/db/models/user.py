from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, INETStr, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.enums import UserRole, UserStatus


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(24))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), default=UserRole.BUYER, nullable=False
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status"), default=UserStatus.PENDING, nullable=False
    )
    kyc_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Bumped on password change / "sign out everywhere"; access tokens carrying a
    # lower `tv` claim are rejected immediately without a DB blocklist.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_users_role_status", "role", "status"),)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def can_bid(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.kyc_verified


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """One row per issued refresh token.

    Rotation chain: presenting token *n* revokes it and mints *n+1* in the same
    ``family_id``.  Presenting an already-rotated token means the credential
    leaked, so the entire family is revoked (see ``services/auth.py``).
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    user_agent: Mapped[str | None] = mapped_column(String(256))
    ip_address: Mapped[str | None] = mapped_column(INETStr)

    user: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (Index("ix_refresh_tokens_user_active", "user_id", "revoked_at"),)
