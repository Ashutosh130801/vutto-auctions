"""Shared FastAPI dependencies: auth, RBAC, rate limits, idempotency."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import settings
from app.core.errors import (
    AuthenticationError,
    IdempotencyConflictError,
    PermissionDeniedError,
    RateLimitedError,
)
from app.core.logging import user_id_ctx
from app.core.ratelimit import RateLimiter
from app.core.security import decode_access_token
from app.db.models.enums import UserRole, UserStatus
from app.db.models.ops import IdempotencyRecord
from app.db.models.user import User
from app.db.session import get_session

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    """Trust ``X-Forwarded-For`` only when we are behind our own proxy.

    ``TRUSTED_PROXY`` is set by the deployment; without it a client could spoof
    the header and bypass IP-scoped rate limits.
    """
    if request.app.state.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError()
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token expired.", code="TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token.", code="TOKEN_INVALID") from exc

    if payload.get("typ") != "access":
        raise AuthenticationError("Wrong token type.", code="TOKEN_INVALID")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token subject.", code="TOKEN_INVALID") from exc

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("Account no longer exists.", code="TOKEN_INVALID")
    if user.token_version != payload.get("tv", 0):
        # Password changed or "sign out everywhere" was used after this token
        # was minted.  Stateless revocation, no blocklist required.
        raise AuthenticationError("Session was revoked.", code="TOKEN_REVOKED")
    if user.status == UserStatus.SUSPENDED:
        raise PermissionDeniedError("This account is suspended.", code="ACCOUNT_SUSPENDED")

    user_id_ctx.set(str(user.id))
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User | None:
    if credentials is None:
        return None
    try:
        return await get_current_user(request, session, credentials)
    except (AuthenticationError, PermissionDeniedError):
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise PermissionDeniedError("Administrator access required.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def rate_limit(scope: str, per_minute: int | None = None) -> Callable[..., Awaitable[None]]:
    """Build a dependency limiting ``scope`` per authenticated user, or per IP
    for anonymous callers."""

    async def _dependency(request: Request, user: OptionalUser = None) -> None:
        if not settings.rate_limit_enabled:
            return
        limiter: RateLimiter = request.app.state.rate_limiter
        identity = f"user:{user.id}" if user else f"ip:{client_ip(request)}"
        limit = per_minute or settings.rate_limit_default_per_minute
        result = await limiter.consume(f"{scope}:{identity}", limit_per_minute=limit)
        if not result.allowed:
            metrics.rate_limit_rejections_total.labels(scope=scope).inc()
            raise RateLimitedError(
                details={
                    "scope": scope,
                    "limit_per_minute": limit,
                    "retry_after_seconds": round(result.retry_after, 2),
                }
            )

    return _dependency


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def load_idempotent_response(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    request_payload: Any,
) -> dict[str, Any] | None:
    """Return a previously stored response for this key, if any.

    Network retries are a fact of life on mobile, and a retried bid that gets
    accepted twice is a real financial bug.  Callers pass
    ``Idempotency-Key``; we replay the original response byte-for-byte.
    """
    if not key:
        return None
    record = (
        await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    if record.request_fingerprint != fingerprint(request_payload):
        raise IdempotencyConflictError(
            details={"key": key, "endpoint": record.endpoint},
        )
    return record.response


async def store_idempotent_response(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    request_payload: Any,
    response: dict[str, Any],
    status_code: int = 201,
) -> None:
    if not key:
        return
    session.add(
        IdempotencyRecord(
            user_id=user_id,
            key=key,
            endpoint=endpoint,
            request_fingerprint=fingerprint(request_payload),
            status_code=status_code,
            response=response,
            created_at=datetime.now(timezone.utc),
        )
    )


IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=128,
        description="Client-generated key making an unsafe request safely retryable.",
    ),
]
