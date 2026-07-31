"""Operator console: inventory, auction scheduling, moderation, audit trail.

Every mutating action here writes an ``audit_logs`` row in the same transaction
as the change itself, so the audit trail cannot drift from reality.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import AdminUser, SessionDep, client_ip
from app.core.errors import ConflictError, NotFoundError
from app.db.models.auction import Auction
from app.db.models.catalog import Bike
from app.db.models.enums import BikeStatus, UserStatus
from app.db.models.ops import AuditLog
from app.db.models.user import User
from app.schemas.auction import AuctionCreate, AuctionDetail
from app.schemas.auth import UserOut
from app.schemas.catalog import BikeCreate, BikeOut, BikeUpdate
from app.schemas.common import Message, Page
from app.services import auction as auction_service
from app.services import queries, serializers

router = APIRouter(prefix="/admin", tags=["admin"])


def audit(
    session,
    request: Request,
    admin: User,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data or {},
            ip_address=client_ip(request),
            request_id=getattr(request.state, "request_id", None),
            created_at=datetime.now(timezone.utc),
        )
    )


class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class UserStatusRequest(BaseModel):
    status: UserStatus
    kyc_verified: bool | None = None


class StatsOut(BaseModel):
    live_auctions: int
    scheduled_auctions: int
    ended_auctions: int
    ending_within_hour: int
    total_bids: int
    gross_merchandise_value: float
    total_users: int


class AuditOut(BaseModel):
    id: uuid.UUID
    actor_email: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    data: dict
    ip_address: str | None
    created_at: datetime


# ------------------------------------------------------------------ stats
@router.get("/stats", response_model=StatsOut, summary="Operations overview")
async def stats(_: AdminUser, session: SessionDep):
    base = await queries.dashboard_stats(session)
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
    return StatsOut(
        **{**base, "gross_merchandise_value": float(base["gross_merchandise_value"])},
        total_users=total_users,
    )


# ------------------------------------------------------------------ bikes
@router.post("/bikes", response_model=BikeOut, status_code=status.HTTP_201_CREATED)
async def create_bike(payload: BikeCreate, admin: AdminUser, request: Request, session: SessionDep):
    existing = (
        await session.execute(
            select(Bike.id).where(Bike.registration_number == payload.registration_number)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("A bike with that registration number already exists.")
    bike = Bike(**payload.model_dump(), status=BikeStatus.READY)
    session.add(bike)
    await session.flush([bike])
    audit(
        session,
        request,
        admin,
        action="bike.created",
        entity_type="bike",
        entity_id=bike.id,
        data={"registration_number": bike.registration_number},
    )
    return BikeOut.model_validate(bike)


@router.get("/bikes", response_model=Page[BikeOut])
async def list_bikes(
    _: AdminUser,
    session: SessionDep,
    status_filter: Annotated[BikeStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
):
    stmt = select(Bike)
    if status_filter:
        stmt = stmt.where(Bike.status == status_filter)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            Bike.make.ilike(pattern)
            | Bike.model.ilike(pattern)
            | Bike.registration_number.ilike(pattern)
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(Bike.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page[BikeOut](
        items=[BikeOut.model_validate(b) for b in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/bikes/{bike_id}", response_model=BikeOut)
async def update_bike(
    bike_id: uuid.UUID,
    payload: BikeUpdate,
    admin: AdminUser,
    request: Request,
    session: SessionDep,
):
    bike = (await session.execute(select(Bike).where(Bike.id == bike_id))).scalar_one_or_none()
    if bike is None:
        raise NotFoundError("Bike not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(bike, field, value)
    audit(
        session,
        request,
        admin,
        action="bike.updated",
        entity_type="bike",
        entity_id=bike.id,
        data={"changed": list(changes)},
    )
    return BikeOut.model_validate(bike)


# --------------------------------------------------------------- auctions
@router.post(
    "/auctions",
    response_model=AuctionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule an auction",
)
async def create_auction(
    payload: AuctionCreate, admin: AdminUser, request: Request, session: SessionDep
):
    auction = await auction_service.create_auction(
        session,
        bike_id=payload.bike_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        start_price=payload.start_price,
        bid_increment=payload.bid_increment,
        reserve_price=payload.reserve_price,
        deposit_required=payload.deposit_required,
        anti_snipe_window_seconds=payload.anti_snipe_window_seconds,
        anti_snipe_extension_seconds=payload.anti_snipe_extension_seconds,
        anti_snipe_max_extensions=payload.anti_snipe_max_extensions,
        notes=payload.notes,
        created_by=admin.id,
    )
    audit(
        session,
        request,
        admin,
        action="auction.created",
        entity_type="auction",
        entity_id=auction.id,
        data={"bike_id": str(payload.bike_id), "start_price": str(payload.start_price)},
    )
    await session.refresh(auction, ["bike"])
    return await _detail(session, auction)


@router.post("/auctions/{auction_id}/cancel", response_model=Message)
async def cancel_auction(
    auction_id: uuid.UUID,
    payload: CancelRequest,
    admin: AdminUser,
    request: Request,
    session: SessionDep,
):
    auction = (
        await session.execute(select(Auction).where(Auction.id == auction_id).with_for_update())
    ).scalar_one_or_none()
    if auction is None:
        raise NotFoundError("Auction not found.")
    await auction_service.cancel_auction(session, auction, reason=payload.reason)
    audit(
        session,
        request,
        admin,
        action="auction.cancelled",
        entity_type="auction",
        entity_id=auction.id,
        data={"reason": payload.reason},
    )
    return Message(message="Auction cancelled and all deposits released.")


@router.post(
    "/auctions/{auction_id}/close",
    response_model=Message,
    summary="Close an auction immediately",
    description="Force-closes a live auction ahead of schedule; outcome rules are unchanged.",
)
async def close_auction(
    auction_id: uuid.UUID, admin: AdminUser, request: Request, session: SessionDep
):
    auction = (
        await session.execute(select(Auction).where(Auction.id == auction_id).with_for_update())
    ).scalar_one_or_none()
    if auction is None:
        raise NotFoundError("Auction not found.")
    await auction_service.close_auction(session, auction)
    audit(
        session,
        request,
        admin,
        action="auction.force_closed",
        entity_type="auction",
        entity_id=auction.id,
        data={"outcome": auction.outcome.value},
    )
    return Message(message=f"Auction closed: {auction.outcome.value}.")


# ------------------------------------------------------------------ users
@router.get("/users", response_model=Page[UserOut])
async def list_users(
    _: AdminUser,
    session: SessionDep,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
):
    stmt = select(User)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(User.email.ilike(pattern) | User.full_name.ilike(pattern))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(User.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/users/{user_id}", response_model=UserOut)
async def set_user_status(
    user_id: uuid.UUID,
    payload: UserStatusRequest,
    admin: AdminUser,
    request: Request,
    session: SessionDep,
):
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found.")
    user.status = payload.status
    if payload.kyc_verified is not None:
        user.kyc_verified = payload.kyc_verified
    if payload.status == UserStatus.SUSPENDED:
        user.token_version += 1  # kick every live session immediately
    audit(
        session,
        request,
        admin,
        action="user.status_changed",
        entity_type="user",
        entity_id=user.id,
        data={"status": payload.status.value, "kyc_verified": user.kyc_verified},
    )
    return UserOut.model_validate(user)


# ------------------------------------------------------------------ audit
@router.get("/audit", response_model=list[AuditOut], summary="Recent audit trail")
async def audit_trail(
    _: AdminUser,
    session: SessionDep,
    entity_type: Annotated[str | None, Query(max_length=48)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    rows = (
        (await session.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [
        AuditOut(
            id=a.id,
            actor_email=a.actor_email,
            action=a.action,
            entity_type=a.entity_type,
            entity_id=a.entity_id,
            data=a.data,
            ip_address=a.ip_address,
            created_at=a.created_at,
        )
        for a in rows
    ]


async def _detail(session, auction: Auction) -> AuctionDetail:
    return AuctionDetail(
        id=auction.id,
        slug=auction.slug,
        title=auction.title,
        status=auction.status,
        outcome=auction.outcome,
        starts_at=auction.starts_at,
        ends_at=auction.ends_at,
        scheduled_ends_at=auction.scheduled_ends_at,
        closed_at=auction.closed_at,
        current_price=auction.current_price,
        start_price=auction.start_price,
        bid_increment=auction.bid_increment,
        deposit_required=auction.deposit_required,
        bid_count=auction.bid_count,
        bidder_count=auction.bidder_count,
        version=auction.version,
        extension_count=auction.extension_count,
        anti_snipe_window_seconds=auction.anti_snipe_window_seconds,
        anti_snipe_extension_seconds=auction.anti_snipe_extension_seconds,
        anti_snipe_max_extensions=auction.anti_snipe_max_extensions,
        has_reserve=auction.reserve_price is not None,
        reserve_met=auction.reserve_met,
        minimum_next_bid=auction.minimum_next_bid,
        winning_amount=auction.winning_amount,
        notes=auction.notes,
        thumbnail=serializers.first_image(auction.bike.images),
        city=auction.bike.city,
        make=auction.bike.make,
        model=auction.bike.model,
        year=auction.bike.year,
        bike=BikeOut.model_validate(auction.bike),
    )
