"""Security-critical auth paths.

These are the branches that only run when something is going wrong — lockout,
suspension, expired and forged tokens — which is exactly why they need tests.
They are the paths an attacker exercises and a normal user never does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from app.core.config import settings
from app.core.errors import (
    AuthenticationError,
    InvalidCredentialsError,
    PermissionDeniedError,
    TokenReuseError,
)
from app.core.security import create_access_token, hash_refresh_token
from app.db.models.enums import UserStatus
from app.db.models.user import RefreshToken, User
from app.services import auth as auth_service
from app.services.auth import MAX_FAILED_LOGINS
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------------- lockout
async def test_repeated_failures_lock_the_account(sessionmaker_, make_user):
    user = await make_user(password="Password@123")
    async with sessionmaker_() as s:
        for _ in range(MAX_FAILED_LOGINS):
            with pytest.raises(InvalidCredentialsError):
                await auth_service.authenticate(s, email=user.email, password="Wrong@12345")
        await s.commit()

    async with sessionmaker_() as s:
        # Even the *correct* password is refused while the lockout holds.
        with pytest.raises(PermissionDeniedError) as exc:
            await auth_service.authenticate(s, email=user.email, password="Password@123")
        assert exc.value.code == "ACCOUNT_LOCKED"
        assert "until" in exc.value.details


async def test_a_successful_login_clears_the_failure_counter(sessionmaker_, make_user):
    user = await make_user(password="Password@123")
    async with sessionmaker_() as s:
        for _ in range(MAX_FAILED_LOGINS - 1):
            with pytest.raises(InvalidCredentialsError):
                await auth_service.authenticate(s, email=user.email, password="Wrong@12345")
        await auth_service.authenticate(s, email=user.email, password="Password@123")
        await s.commit()

    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        assert fresh.failed_login_attempts == 0
        assert fresh.locked_until is None
        assert fresh.last_login_at is not None


async def test_lockout_expires(sessionmaker_, make_user):
    user = await make_user(password="Password@123")
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        fresh.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()

    async with sessionmaker_() as s:
        assert await auth_service.authenticate(s, email=user.email, password="Password@123")


# -------------------------------------------------------------- suspension
async def test_suspended_accounts_cannot_authenticate(sessionmaker_, make_user):
    user = await make_user()
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        fresh.status = UserStatus.SUSPENDED
        await s.commit()

    async with sessionmaker_() as s:
        with pytest.raises(PermissionDeniedError) as exc:
            await auth_service.authenticate(s, email=user.email, password=user.raw_password)
        assert exc.value.code == "ACCOUNT_SUSPENDED"


# ------------------------------------------------------------ refresh flow
async def test_expired_refresh_tokens_are_rejected(sessionmaker_, make_user):
    user = await make_user()
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        pair = await auth_service.issue_tokens(s, fresh)
        raw = pair.refresh_token
        await s.commit()

    async with sessionmaker_() as s:
        record = (
            await s.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
            )
        ).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()

    async with sessionmaker_() as s:
        with pytest.raises(AuthenticationError) as exc:
            await auth_service.rotate_refresh_token(s, raw_token=raw)
        assert exc.value.code == "REFRESH_TOKEN_EXPIRED"


async def test_unknown_refresh_tokens_are_rejected(sessionmaker_):
    async with sessionmaker_() as s:
        with pytest.raises(AuthenticationError) as exc:
            await auth_service.rotate_refresh_token(s, raw_token="not-a-real-token")
        assert exc.value.code == "INVALID_REFRESH_TOKEN"


async def test_reuse_revokes_the_family_durably(sessionmaker_, make_user):
    """The revocation must survive the exception that rejects the request.

    The request-scoped session rolls back on error, so a naive implementation
    would quietly undo its own security response.
    """
    user = await make_user()
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        first = (await auth_service.issue_tokens(s, fresh)).refresh_token
        await s.commit()

    async with sessionmaker_() as s:
        second = (await auth_service.rotate_refresh_token(s, raw_token=first)).refresh_token
        await s.commit()

    async with sessionmaker_() as s:
        with pytest.raises(TokenReuseError):
            await auth_service.rotate_refresh_token(s, raw_token=first)

    # A brand new session — proving the revocation was actually committed.
    async with sessionmaker_() as s:
        live = (
            (
                await s.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == [], "the whole family must be dead, including the newest token"

    async with sessionmaker_() as s:
        with pytest.raises(AuthenticationError):
            await auth_service.rotate_refresh_token(s, raw_token=second)


async def test_rotation_preserves_the_family(sessionmaker_, make_user):
    user = await make_user()
    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        raw = (await auth_service.issue_tokens(s, fresh)).refresh_token
        await s.commit()

    async with sessionmaker_() as s:
        await auth_service.rotate_refresh_token(s, raw_token=raw)
        await s.commit()

    async with sessionmaker_() as s:
        families = {
            r.family_id
            for r in (await s.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
            .scalars()
            .all()
        }
        assert len(families) == 1, "rotation must stay within one family"


# --------------------------------------------------------------- JWT edges
async def test_a_token_signed_with_the_wrong_key_is_rejected(client, make_user):
    user = await make_user()
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "role": "ADMIN",
            "tv": 0,
            "typ": "access",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
            "iss": settings.app_name,
        },
        "an-attackers-key",
        algorithm="HS256",
    )
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_an_expired_access_token_is_rejected(client, make_user):
    user = await make_user()
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "role": "BUYER",
            "tv": 0,
            "typ": "access",
            "exp": int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()),
            "iss": settings.app_name,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_a_refresh_token_cannot_be_used_as_an_access_token(client, make_user):
    """`typ` must be checked, or a long-lived refresh token becomes a
    long-lived API key."""
    user = await make_user()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": user.raw_password},
    )
    refresh = login.json()["refresh_token"]
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


async def test_changing_the_password_invalidates_old_access_tokens(
    client, sessionmaker_, make_user
):
    user = await make_user(password="Password@123")
    login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "Password@123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "Password@123", "new_password": "BrandNew@456"},
        headers=headers,
    )
    assert changed.status_code == 200

    after = await client.get("/api/v1/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "TOKEN_REVOKED"

    relogin = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "BrandNew@456"}
    )
    assert relogin.status_code == 200


async def test_token_version_bump_invalidates_a_previously_valid_token(
    client, sessionmaker_, make_user
):
    user = await make_user()
    token, _ = create_access_token(subject=str(user.id), role="BUYER", token_version=0)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200

    async with sessionmaker_() as s:
        fresh = await s.get(User, user.id)
        fresh.token_version += 1
        await s.commit()

    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
