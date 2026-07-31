"""Everything scoped to the signed-in user: profile, deposits, activity, alerts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.deps import CurrentUser, SessionDep
from app.core.errors import ValidationError
from app.db.models.enums import AuctionStatus, DepositTxnType
from app.db.models.finance import DepositAccount, DepositTransaction
from app.db.models.ops import Notification
from app.schemas.auction import AuctionSummary
from app.schemas.auth import UserOut
from app.schemas.common import Message, Page
from app.services import queries

router = APIRouter(prefix="/me", tags=["me"])


class DepositOut(BaseModel):
    balance: Decimal
    held: Decimal
    available: Decimal


class TopUpRequest(BaseModel):
    """Stands in for a payment-gateway callback.

    In production this endpoint would not exist: the ledger would be credited by
    a signed webhook from the PSP after funds actually settle. Documented in
    ASSUMPTIONS.md.
    """

    amount: Decimal = Field(gt=0, le=Decimal("1000000"))


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    body: str
    data: dict
    read_at: datetime | None
    created_at: datetime


@router.get("", response_model=UserOut, summary="Current profile")
async def profile(user: CurrentUser):
    return UserOut.model_validate(user)


@router.get("/deposit", response_model=DepositOut, summary="Deposit balance")
async def deposit(user: CurrentUser, session: SessionDep):
    account = (
        await session.execute(select(DepositAccount).where(DepositAccount.user_id == user.id))
    ).scalar_one_or_none()
    if account is None:
        account = DepositAccount(user_id=user.id)
        session.add(account)
        await session.flush([account])
    return DepositOut(balance=account.balance, held=account.held, available=account.available)


@router.post("/deposit/top-up", response_model=DepositOut, summary="Add to deposit")
async def top_up(payload: TopUpRequest, user: CurrentUser, session: SessionDep):
    now = datetime.now(timezone.utc)
    account = (
        await session.execute(
            select(DepositAccount).where(DepositAccount.user_id == user.id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        account = DepositAccount(user_id=user.id)
        session.add(account)
        await session.flush([account])
    account.balance = account.balance + payload.amount
    session.add(
        DepositTransaction(
            user_id=user.id,
            type=DepositTxnType.TOPUP,
            amount=payload.amount,
            reference="simulated-psd",
            created_at=now,
        )
    )
    return DepositOut(balance=account.balance, held=account.held, available=account.available)


@router.post("/deposit/withdraw", response_model=DepositOut, summary="Withdraw free funds")
async def withdraw(payload: TopUpRequest, user: CurrentUser, session: SessionDep):
    now = datetime.now(timezone.utc)
    account = (
        await session.execute(
            select(DepositAccount).where(DepositAccount.user_id == user.id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None or account.available < payload.amount:
        raise ValidationError(
            "You cannot withdraw funds that are held against a leading bid.",
            code="INSUFFICIENT_AVAILABLE_BALANCE",
            details={"available": f"{(account.available if account else 0):.2f}"},
        )
    account.balance = account.balance - payload.amount
    session.add(
        DepositTransaction(
            user_id=user.id,
            type=DepositTxnType.REFUND,
            amount=payload.amount,
            reference="withdrawal",
            created_at=now,
        )
    )
    return DepositOut(balance=account.balance, held=account.held, available=account.available)


@router.get("/bids", response_model=Page[AuctionSummary], summary="Auctions you have bid on")
async def my_bids(
    user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[AuctionStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=60)] = 20,
):
    items, total = await queries.list_auctions(
        session, bid_by=user.id, status=status_filter, page=page, page_size=page_size
    )
    return Page[AuctionSummary](
        items=[AuctionSummary(**i) for i in items], total=total, page=page, page_size=page_size
    )


@router.get("/watchlist", response_model=Page[AuctionSummary], summary="Watched auctions")
async def my_watchlist(
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=60)] = 20,
):
    items, total = await queries.list_auctions(
        session, watched_by=user.id, page=page, page_size=page_size
    )
    return Page[AuctionSummary](
        items=[AuctionSummary(**i) for i in items], total=total, page=page, page_size=page_size
    )


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(
    user: CurrentUser,
    session: SessionDep,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        NotificationOut(
            id=n.id,
            type=n.type.value,
            title=n.title,
            body=n.body,
            data=n.data,
            read_at=n.read_at,
            created_at=n.created_at,
        )
        for n in rows
    ]


@router.post("/notifications/read", response_model=Message)
async def mark_read(user: CurrentUser, session: SessionDep):
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    return Message(message="Notifications marked as read.")
