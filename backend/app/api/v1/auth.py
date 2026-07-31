from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status

from app.core.config import settings
from app.core.deps import CurrentUser, SessionDep, client_ip, rate_limit
from app.db.models.ops import AuditLog
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import Message
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _audit(request: Request, session, *, actor, action: str, data: dict | None = None) -> None:
    session.add(
        AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            action=action,
            entity_type="user",
            entity_id=getattr(actor, "id", None),
            data=data or {},
            ip_address=client_ip(request),
            request_id=getattr(request.state, "request_id", None),
            created_at=datetime.now(timezone.utc),
        )
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth", settings.rate_limit_auth_per_minute))],
    summary="Create a buyer account and sign in",
)
async def register(payload: RegisterRequest, request: Request, session: SessionDep):
    user = await auth_service.register(
        session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        phone=payload.phone,
    )
    tokens = await auth_service.issue_tokens(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    _audit(request, session, actor=user, action="user.registered")
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth", settings.rate_limit_auth_per_minute))],
    summary="Exchange credentials for a token pair",
)
async def login(payload: LoginRequest, request: Request, session: SessionDep):
    user = await auth_service.authenticate(session, email=payload.email, password=payload.password)
    tokens = await auth_service.issue_tokens(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    _audit(request, session, actor=user, action="user.login")
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth", settings.rate_limit_auth_per_minute * 3))],
    summary="Rotate a refresh token",
    description=(
        "Refresh tokens are single use. Presenting one that was already rotated "
        "revokes the entire token family, on the assumption it was stolen."
    ),
)
async def refresh(payload: RefreshRequest, request: Request, session: SessionDep):
    tokens = await auth_service.rotate_refresh_token(
        session,
        raw_token=payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
        user=UserOut.model_validate(tokens.user),
    )


@router.post("/logout", response_model=Message, summary="Revoke one refresh token")
async def logout(payload: RefreshRequest, session: SessionDep):
    await auth_service.revoke_refresh_token(session, raw_token=payload.refresh_token)
    return Message(message="Signed out.")


@router.post("/logout-all", response_model=Message, summary="Revoke every session")
async def logout_all(user: CurrentUser, request: Request, session: SessionDep):
    await auth_service.revoke_all_sessions(session, user)
    _audit(request, session, actor=user, action="user.logout_all")
    return Message(message="All sessions revoked.")


@router.post("/change-password", response_model=Message)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, request: Request, session: SessionDep
):
    await auth_service.change_password(
        session,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    _audit(request, session, actor=user, action="user.password_changed")
    return Message(message="Password updated. Please sign in again.")
