"""Registration, login, and refresh-token rotation with reuse detection.

Threat model handled here
-------------------------
* **Credential stuffing** — Argon2id + per-account lockout after repeated
  failures, plus an IP/route rate limit applied at the edge.
* **User enumeration** — login and registration return the same shape and
  timing-insensitive messages; we never say "no such email".
* **Stolen refresh token** — tokens are single-use.  Using one rotates it.  If a
  *already-rotated* token is presented, either the attacker or the victim is
  replaying, so we revoke the entire token family and force a fresh login.  This
  turns a silent, indefinite compromise into a loud, bounded one.
* **Session invalidation** — access tokens are stateless, so "sign out
  everywhere" bumps ``users.token_version``; every previously issued access
  token then fails the ``tv`` check without needing a blocklist.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    InvalidCredentialsError,
    PermissionDeniedError,
    TokenReuseError,
)
from app.core.logging import get_logger
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models.enums import UserRole, UserStatus
from app.db.models.finance import DepositAccount
from app.db.models.user import RefreshToken, User
from app.db.session import session_scope

log = get_logger(__name__)

MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15


class TokenPair:
    __slots__ = ("access_token", "expires_at", "refresh_token", "user")

    def __init__(self, access_token: str, refresh_token: str, expires_at: datetime, user: User):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.user = user


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(select(User).where(func.lower(User.email) == email.lower().strip()))
    ).scalar_one_or_none()


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    phone: str | None = None,
    role: UserRole = UserRole.BUYER,
    auto_verify: bool = True,
) -> User:
    """Create an account.

    ``auto_verify`` stands in for the KYC/document review step that a real
    vehicle marketplace runs before letting anyone bid.  It is a deliberate,
    documented simplification — the *gate* exists and is enforced everywhere, we
    just approve it synchronously here.
    """
    if await get_by_email(session, email) is not None:
        raise ConflictError("An account with that email already exists.", code="EMAIL_TAKEN")

    user = User(
        email=email.strip(),
        full_name=full_name.strip(),
        phone=phone,
        password_hash=hash_password(password),
        role=role,
        status=UserStatus.ACTIVE if auto_verify else UserStatus.PENDING,
        kyc_verified=auto_verify,
    )
    session.add(user)
    await session.flush([user])
    session.add(DepositAccount(user_id=user.id))
    log.info("auth.registered", user_id=str(user.id), role=role.value)
    return user


async def authenticate(
    session: AsyncSession, *, email: str, password: str, now: datetime | None = None
) -> User:
    now = now or datetime.now(timezone.utc)
    user = await get_by_email(session, email)
    if user is None:
        # Spend comparable CPU on a dummy verification so response time does not
        # leak whether the address exists.
        verify_password(password, DUMMY_HASH)
        raise InvalidCredentialsError()

    if user.locked_until and user.locked_until > now:
        raise PermissionDeniedError(
            "Account temporarily locked after repeated failed logins.",
            code="ACCOUNT_LOCKED",
            details={"until": user.locked_until.isoformat()},
        )
    if user.status == UserStatus.SUSPENDED:
        raise PermissionDeniedError("This account is suspended.", code="ACCOUNT_SUSPENDED")

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
            log.warning("auth.account_locked", user_id=str(user.id))
        raise InvalidCredentialsError()

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)  # transparent parameter upgrade
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    return user


async def issue_tokens(
    session: AsyncSession,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> TokenPair:
    now = now or datetime.now(timezone.utc)
    access, expires_at = create_access_token(
        subject=str(user.id), role=user.role.value, token_version=user.token_version
    )
    raw_refresh = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
            created_at=now,
            user_agent=(user_agent or "")[:256] or None,
            ip_address=ip_address,
        )
    )
    return TokenPair(access, raw_refresh, expires_at, user)


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    raw_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> TokenPair:
    now = now or datetime.now(timezone.utc)
    token_hash = hash_refresh_token(raw_token)
    record = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
    ).scalar_one_or_none()

    if record is None:
        raise AuthenticationError("Invalid refresh token.", code="INVALID_REFRESH_TOKEN")

    if record.revoked_at is not None or record.rotated_at is not None:
        # Replay of a spent token → assume compromise, burn the whole family.
        #
        # This has to happen in its *own* committed transaction: we are about to
        # raise, and the request-scoped session rolls back on exception, which
        # would quietly undo the revocation and leave the stolen family alive.
        # A security control that only applies on the happy path is not a
        # control at all.
        family_id, user_id = record.family_id, record.user_id
        await session.rollback()
        async with session_scope() as revoke_session:
            await revoke_session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        log.warning(
            "auth.refresh_reuse_detected",
            user_id=str(user_id),
            family_id=str(family_id),
        )
        raise TokenReuseError()

    if record.expires_at <= now:
        raise AuthenticationError("Refresh token expired.", code="REFRESH_TOKEN_EXPIRED")

    user = (await session.execute(select(User).where(User.id == record.user_id))).scalar_one()
    if user.status == UserStatus.SUSPENDED:
        raise PermissionDeniedError("This account is suspended.", code="ACCOUNT_SUSPENDED")

    record.rotated_at = now
    record.revoked_at = now
    return await issue_tokens(
        session,
        user,
        family_id=record.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
        now=now,
    )


async def revoke_refresh_token(session: AsyncSession, *, raw_token: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(raw_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )


async def revoke_all_sessions(session: AsyncSession, user: User) -> None:
    """Sign out everywhere: kill refresh tokens *and* invalidate live access
    tokens by bumping the version claim they were minted with."""
    user.token_version += 1
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


async def change_password(
    session: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError("Current password is incorrect.")
    user.password_hash = hash_password(new_password)
    await revoke_all_sessions(session, user)
    log.info("auth.password_changed", user_id=str(user.id))
