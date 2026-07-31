"""Public auction browsing, the live auction room, and bid submission."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, status
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    IdempotencyKey,
    OptionalUser,
    SessionDep,
    client_ip,
    load_idempotent_response,
    rate_limit,
    store_idempotent_response,
)
from app.core.errors import ConflictError, NotFoundError
from app.db.models.auction import Auction
from app.db.models.bidding import Watchlist
from app.db.models.catalog import Bike
from app.db.models.enums import AuctionStatus
from app.db.session import session_scope
from app.schemas.auction import (
    AuctionDetail,
    AuctionSummary,
    BidAccepted,
    BidCreate,
    BidOut,
    LedgerVerdict,
)
from app.schemas.catalog import BikeOut
from app.schemas.common import Message, Page
from app.services import bidding as bidding_service
from app.services import queries, serializers

router = APIRouter(tags=["auctions"])


# ---------------------------------------------------------------- browsing
@router.get(
    "/auctions",
    response_model=Page[AuctionSummary],
    summary="Browse auctions",
    description=(
        "Filterable, sortable listing. Defaults to auctions ending soonest, "
        "which is what a bidder almost always wants to see first."
    ),
)
async def list_auctions(
    session: SessionDep,
    status_filter: Annotated[AuctionStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    city: Annotated[str | None, Query(max_length=64)] = None,
    make: Annotated[str | None, Query(max_length=64)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    min_year: Annotated[int | None, Query(ge=1980, le=2100)] = None,
    max_year: Annotated[int | None, Query(ge=1980, le=2100)] = None,
    sort: Annotated[
        str, Query(pattern="^(ending_soon|newest|price_asc|price_desc|most_bids)$")
    ] = "ending_soon",
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=60)] = 20,
):
    items, total = await queries.list_auctions(
        session,
        status=status_filter,
        search=search,
        city=city,
        make=make,
        min_price=min_price,
        max_price=max_price,
        min_year=min_year,
        max_year=max_year,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page[AuctionSummary](
        items=[AuctionSummary(**i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/auctions/{ident}",
    response_model=AuctionDetail,
    summary="Auction detail",
    description="Accepts either the auction UUID or its slug.",
)
async def get_auction(
    ident: str, session: SessionDep, response: Response, user: OptionalUser = None
):
    auction = await queries.get_auction(session, ident)
    if auction is None:
        raise NotFoundError("Auction not found.")

    detail: dict[str, Any] = {
        "id": auction.id,
        "slug": auction.slug,
        "title": auction.title,
        "status": auction.status,
        "outcome": auction.outcome,
        "starts_at": auction.starts_at,
        "ends_at": auction.ends_at,
        "scheduled_ends_at": auction.scheduled_ends_at,
        "closed_at": auction.closed_at,
        "current_price": auction.current_price,
        "start_price": auction.start_price,
        "bid_increment": auction.bid_increment,
        "deposit_required": auction.deposit_required,
        "bid_count": auction.bid_count,
        "bidder_count": auction.bidder_count,
        "version": auction.version,
        "extension_count": auction.extension_count,
        "anti_snipe_window_seconds": auction.anti_snipe_window_seconds,
        "anti_snipe_extension_seconds": auction.anti_snipe_extension_seconds,
        "anti_snipe_max_extensions": auction.anti_snipe_max_extensions,
        "has_reserve": auction.reserve_price is not None,
        "reserve_met": auction.reserve_met,
        "minimum_next_bid": auction.minimum_next_bid,
        "winning_amount": auction.winning_amount,
        "notes": auction.notes,
        "thumbnail": serializers.first_image(auction.bike.images),
        "city": auction.bike.city,
        "make": auction.bike.make,
        "model": auction.bike.model,
        "year": auction.bike.year,
        "bike": BikeOut.model_validate(auction.bike),
    }
    if user is not None:
        detail["your_max_bid"] = await queries.user_max_for(session, auction.id, user.id)
        detail["you_are_leading"] = auction.leading_user_id == user.id
        detail["you_are_watching"] = await queries.is_watching(session, auction.id, user.id)

    # Live auctions must never be cached; ended ones are immutable.
    response.headers["Cache-Control"] = (
        "public, max-age=60" if auction.status == AuctionStatus.ENDED else "no-store"
    )
    return AuctionDetail(**detail)


@router.get(
    "/auctions/{auction_id}/bids",
    response_model=list[BidOut],
    summary="Bid history",
    description=(
        "Newest first, keyset-paginated by `sequence`. Bidders are shown as "
        "per-auction aliases; nobody's private maximum is ever exposed."
    ),
)
async def list_bids(
    auction_id: uuid.UUID,
    session: SessionDep,
    user: OptionalUser = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_sequence: Annotated[int | None, Query(ge=1)] = None,
):
    bids = await queries.bid_history(
        session, auction_id, limit=limit, before_sequence=before_sequence
    )
    return [
        BidOut(
            **serializers.public_bid(
                b, bidder_alias=serializers.bidder_alias(auction_id, b.bidder_id)
            ),
            is_you=bool(user and b.bidder_id == user.id),
        )
        for b in bids
    ]


@router.get(
    "/auctions/{auction_id}/ledger",
    response_model=LedgerVerdict,
    summary="Verify the bid ledger",
    description=(
        "Recomputes the SHA-256 hash chain over every bid. Any retroactive "
        "edit, insertion, deletion or reorder breaks the chain and is reported "
        "with the sequence number where verification failed."
    ),
)
async def verify_ledger(auction_id: uuid.UUID, session: SessionDep):
    result = await bidding_service.verify_ledger(session, auction_id)
    return LedgerVerdict(
        valid=result.valid,
        entries_checked=result.entries_checked,
        head_hash=result.head_hash,
        broken_at_sequence=result.broken_at_sequence,
        reason=result.reason,
    )


# ---------------------------------------------------------------- bidding
@router.post(
    "/auctions/{auction_id}/bids",
    response_model=BidAccepted,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("bid", settings.rate_limit_bid_per_minute))],
    summary="Place a proxy bid",
    description=(
        "`max_amount` is the **maximum** you authorise, not the price you pay. "
        "The engine bids only as high as needed to lead.\n\n"
        "Send an `Idempotency-Key` header to make retries safe: a repeated key "
        "replays the original response instead of placing a second bid."
    ),
    responses={
        409: {"description": "Bid too low, auction not live, or deposit short"},
        429: {"description": "Rate limited"},
    },
)
async def place_bid(
    auction_id: uuid.UUID,
    payload: BidCreate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: IdempotencyKey = None,
):
    endpoint = "POST /auctions/{auction_id}/bids"
    request_payload = {
        "auction_id": str(auction_id),
        "max_amount": str(payload.max_amount),
    }
    cached = await load_idempotent_response(
        session,
        user_id=user.id,
        key=idempotency_key,
        endpoint=endpoint,
        request_payload=request_payload,
    )
    if cached is not None:
        return BidAccepted(**cached)

    if payload.expected_version is not None:
        auction = await queries.get_auction(session, str(auction_id))
        if auction is not None and auction.version != payload.expected_version:
            raise ConflictError(
                "The auction moved on before your bid was submitted.",
                code="STALE_VERSION",
                details={
                    "expected_version": payload.expected_version,
                    "actual_version": auction.version,
                    "current_price": f"{auction.current_price:.2f}",
                },
            )

    result = await bidding_service.place_bid(
        session,
        auction_id=auction_id,
        bidder=user,
        max_amount=payload.max_amount,
        idempotency_key=idempotency_key,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    body = bidding_service.as_dict(result)
    await store_idempotent_response(
        session,
        user_id=user.id,
        key=idempotency_key,
        endpoint=endpoint,
        request_payload=request_payload,
        response=body,
    )
    return BidAccepted(**body)


# ---------------------------------------------------------------- watchlist
@router.put("/auctions/{auction_id}/watch", response_model=Message)
async def watch(auction_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    auction = await queries.get_auction(session, str(auction_id))
    if auction is None:
        raise NotFoundError("Auction not found.")
    if not await queries.is_watching(session, auction_id, user.id):
        session.add(
            Watchlist(
                user_id=user.id,
                auction_id=auction_id,
                created_at=datetime.now(timezone.utc),
            )
        )
    return Message(message="Added to your watchlist.")


@router.delete("/auctions/{auction_id}/watch", response_model=Message)
async def unwatch(auction_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    await session.execute(
        delete(Watchlist).where(Watchlist.user_id == user.id, Watchlist.auction_id == auction_id)
    )
    return Message(message="Removed from your watchlist.")


# ---------------------------------------------------------------- realtime
@router.websocket("/auctions/{auction_id}/stream")
async def auction_stream(
    websocket: WebSocket,
    auction_id: uuid.UUID,
    token: Annotated[str | None, Query()] = None,
):
    """Live auction feed.

    Frames: ``snapshot``, ``auction.bid_placed``, ``auction.extended``,
    ``auction.ended``, ``presence``, ``heartbeat``. Every frame carries
    ``server_time`` so the client can correct for local clock skew — essential
    for an honest countdown in the final seconds.

    The token is passed as a query parameter because browsers cannot set
    headers on a WebSocket handshake. It is short-lived (15 min) and the socket
    is read-only, so the exposure is bounded; bids still go over authenticated
    HTTPS. See ASSUMPTIONS.md.
    """
    from app.core.security import decode_access_token
    from app.realtime.manager import serve_auction_socket

    user_id: uuid.UUID | None = None
    if token:
        try:
            payload = decode_access_token(token)
            if payload.get("typ") == "access":
                user_id = uuid.UUID(payload["sub"])
        except Exception:
            user_id = None

    async with session_scope() as session:
        row = (
            await session.execute(
                select(*queries.LIST_COLUMNS)
                .join(Bike, Bike.id == Auction.bike_id)
                .where(Auction.id == auction_id)
            )
        ).first()
        if row is None:
            await websocket.close(code=4404, reason="Auction not found")
            return
        snapshot = queries.row_to_summary(row)

    await serve_auction_socket(
        websocket,
        bus=websocket.app.state.bus,
        auction_id=auction_id,
        user_id=user_id,
        initial_state={"auction": {k: serializers.jsonable(v) for k, v in snapshot.items()}},
    )
