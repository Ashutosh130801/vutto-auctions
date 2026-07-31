"""Read-side query helpers.

Kept apart from the write services because the shapes differ: writes work with
locked aggregates, reads want denormalised joins and no locks.  Every list query
selects an explicit column tuple rather than whole entities, so adding a large
column (an inspection report, a description) never silently inflates a list
endpoint's payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from app.db.models.auction import Auction
from app.db.models.bidding import Bid, Watchlist
from app.db.models.catalog import Bike
from app.db.models.enums import AuctionOutcome, AuctionStatus, BidStatus

LIST_COLUMNS = (
    Auction.id,
    Auction.slug,
    Auction.title,
    Auction.status,
    Auction.outcome,
    Auction.starts_at,
    Auction.ends_at,
    Auction.current_price,
    Auction.start_price,
    Auction.bid_increment,
    Auction.deposit_required,
    Auction.reserve_price,
    Auction.bid_count,
    Auction.bidder_count,
    Auction.version,
    Bike.images,
    Bike.city,
    Bike.make,
    Bike.model,
    Bike.year,
)


def row_to_summary(row: Any) -> dict[str, Any]:
    images = row.images or []
    reserve: Decimal | None = row.reserve_price
    return {
        "id": row.id,
        "slug": row.slug,
        "title": row.title,
        "status": row.status,
        "outcome": row.outcome,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "current_price": row.current_price,
        "start_price": row.start_price,
        "bid_increment": row.bid_increment,
        "deposit_required": row.deposit_required,
        "bid_count": row.bid_count,
        "bidder_count": row.bidder_count,
        "version": row.version,
        "has_reserve": reserve is not None,
        "reserve_met": reserve is None or row.current_price >= reserve,
        "minimum_next_bid": (
            row.start_price if row.bid_count == 0 else row.current_price + row.bid_increment
        ),
        "thumbnail": images[0] if images else None,
        "city": row.city,
        "make": row.make,
        "model": row.model,
        "year": row.year,
    }


SORTS: dict[str, tuple[UnaryExpression[Any], ...]] = {
    "ending_soon": (Auction.ends_at.asc(),),
    "newest": (Auction.created_at.desc(),),
    "price_asc": (Auction.current_price.asc(),),
    "price_desc": (Auction.current_price.desc(),),
    "most_bids": (Auction.bid_count.desc(), Auction.ends_at.asc()),
}


def _base_query() -> Select[Any]:
    return select(*LIST_COLUMNS).join(Bike, Bike.id == Auction.bike_id)


def _apply_filters(
    stmt: Select[Any],
    *,
    status: AuctionStatus | None,
    search: str | None,
    city: str | None,
    make: str | None,
    min_price: Decimal | None,
    max_price: Decimal | None,
    max_year: int | None,
    min_year: int | None,
) -> Select[Any]:
    if status is not None:
        stmt = stmt.where(Auction.status == status)
    if city:
        stmt = stmt.where(func.lower(Bike.city) == city.lower())
    if make:
        stmt = stmt.where(func.lower(Bike.make) == make.lower())
    if min_price is not None:
        stmt = stmt.where(Auction.current_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Auction.current_price <= max_price)
    if min_year is not None:
        stmt = stmt.where(Bike.year >= min_year)
    if max_year is not None:
        stmt = stmt.where(Bike.year <= max_year)
    if search:
        # ILIKE with a trailing wildcard is index-friendly enough at this scale;
        # the migration path to tsvector/pg_trgm is noted in ARCHITECTURE.md.
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Auction.title.ilike(pattern),
                Bike.make.ilike(pattern),
                Bike.model.ilike(pattern),
                Bike.registration_number.ilike(pattern),
            )
        )
    return stmt


async def list_auctions(
    session: AsyncSession,
    *,
    status: AuctionStatus | None = None,
    search: str | None = None,
    city: str | None = None,
    make: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    sort: str = "ending_soon",
    page: int = 1,
    page_size: int = 20,
    watched_by: uuid.UUID | None = None,
    bid_by: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], int]:
    stmt = _apply_filters(
        _base_query(),
        status=status,
        search=search,
        city=city,
        make=make,
        min_price=min_price,
        max_price=max_price,
        min_year=min_year,
        max_year=max_year,
    )
    if watched_by is not None:
        stmt = stmt.join(
            Watchlist,
            and_(Watchlist.auction_id == Auction.id, Watchlist.user_id == watched_by),
        )
    if bid_by is not None:
        stmt = stmt.where(
            select(Bid.id).where(Bid.auction_id == Auction.id, Bid.bidder_id == bid_by).exists()
        )

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    order = SORTS.get(sort, SORTS["ending_soon"])
    stmt = stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(stmt)).all()
    return [row_to_summary(r) for r in rows], total


async def get_auction(session: AsyncSession, ident: str) -> Auction | None:
    """Look up by UUID or by slug — friendly URLs without a second endpoint."""
    try:
        return (
            await session.execute(select(Auction).where(Auction.id == uuid.UUID(ident)))
        ).scalar_one_or_none()
    except ValueError:
        return (
            await session.execute(select(Auction).where(Auction.slug == ident))
        ).scalar_one_or_none()


async def bid_history(
    session: AsyncSession,
    auction_id: uuid.UUID,
    *,
    limit: int = 50,
    before_sequence: int | None = None,
) -> Sequence[Bid]:
    stmt = select(Bid).where(Bid.auction_id == auction_id)
    if before_sequence is not None:
        stmt = stmt.where(Bid.sequence < before_sequence)
    stmt = stmt.order_by(Bid.sequence.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def user_max_for(
    session: AsyncSession, auction_id: uuid.UUID, user_id: uuid.UUID
) -> Decimal | None:
    return (
        await session.execute(
            select(func.max(Bid.max_amount)).where(
                Bid.auction_id == auction_id, Bid.bidder_id == user_id
            )
        )
    ).scalar_one_or_none()


async def is_watching(session: AsyncSession, auction_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return bool(
        (
            await session.execute(
                select(Watchlist.user_id).where(
                    Watchlist.auction_id == auction_id, Watchlist.user_id == user_id
                )
            )
        ).scalar_one_or_none()
    )


async def dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    """One round trip for the admin overview.

    Aggregate filters (``count(*) FILTER (WHERE ...)``) let Postgres compute all
    the status buckets in a single sequential pass instead of five queries.
    """
    now = datetime.now(timezone.utc)
    live, scheduled, ended, ending_soon = (
        await session.execute(
            select(
                func.count().filter(Auction.status == AuctionStatus.LIVE),
                func.count().filter(Auction.status == AuctionStatus.SCHEDULED),
                func.count().filter(Auction.status == AuctionStatus.ENDED),
                func.count().filter(
                    Auction.status == AuctionStatus.LIVE,
                    Auction.ends_at <= now + timedelta(hours=1),
                ),
            ).select_from(Auction)
        )
    ).one()

    total_bids = (await session.execute(select(func.count(Bid.id)))).scalar_one()
    gmv = (
        await session.execute(
            select(func.coalesce(func.sum(Auction.winning_amount), 0)).where(
                Auction.outcome == AuctionOutcome.SOLD
            )
        )
    ).scalar_one()

    return {
        "live_auctions": live,
        "scheduled_auctions": scheduled,
        "ended_auctions": ended,
        "ending_within_hour": ending_soon,
        "total_bids": total_bids,
        "gross_merchandise_value": gmv,
    }


async def leading_bids_for_user(session: AsyncSession, user_id: uuid.UUID) -> Sequence[uuid.UUID]:
    return (
        (
            await session.execute(
                select(Bid.auction_id).where(
                    Bid.bidder_id == user_id, Bid.status == BidStatus.LEADING
                )
            )
        )
        .scalars()
        .all()
    )
