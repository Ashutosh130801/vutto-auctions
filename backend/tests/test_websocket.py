"""WebSocket protocol test.

Kept in its own module and written synchronously on purpose. Starlette's
``TestClient`` is the only client here that speaks the WebSocket handshake, and
it drives the app on a private event loop in a worker thread. asyncpg pins
connections to the loop that created them, so this test builds its fixtures
*inside* that same loop through ``client.portal`` rather than reusing the async
fixtures from ``conftest``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.conftest import DATABASE_URL


async def _bootstrap() -> dict[str, str]:
    """Create a live auction and a funded buyer on the caller's event loop."""
    from app.db import session as session_module
    from app.db.models import Base
    from app.db.models.catalog import Bike
    from app.db.models.enums import BikeStatus
    from app.db.models.finance import DepositAccount
    from app.services import auction as auction_service
    from app.services import auth as auth_service
    from sqlalchemy import select

    engine = create_async_engine(DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_module.configure(engine)  # the app under test uses this engine too

    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    now = datetime.now(timezone.utc)
    suffix = uuid.uuid4().hex[:8]

    async with maker() as s:
        user = await auth_service.register(
            s,
            email=f"ws-{suffix}@example.com",
            password="Password@123",
            full_name="Socket Tester",
        )
        await s.flush()
        account = (
            await s.execute(select(DepositAccount).where(DepositAccount.user_id == user.id))
        ).scalar_one()
        account.balance = Decimal("100000")

        bike = Bike(
            registration_number=f"WS{suffix.upper()}",
            make="Yamaha",
            model="MT-15",
            year=2023,
            engine_cc=155,
            odometer_km=8000,
            city="Chennai",
            condition_grade="A",
            inspection_score=95,
            inspection={},
            images=["https://example.com/a.jpg"],
            estimated_value=Decimal("130000"),
            status=BikeStatus.READY,
        )
        s.add(bike)
        await s.flush()

        auction = await auction_service.create_auction(
            s,
            bike_id=bike.id,
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(hours=2),
            start_price=Decimal("50000"),
            bid_increment=Decimal("1000"),
            reserve_price=None,
            deposit_required=Decimal("0"),
            anti_snipe_window_seconds=120,
            anti_snipe_extension_seconds=120,
            anti_snipe_max_extensions=20,
            notes=None,
            created_by=None,
        )
        await auction_service.start_due_auctions(s, now=now)
        await s.commit()
        return {
            "auction_id": str(auction.id),
            "email": user.email,
            "password": "Password@123",
        }


def test_websocket_delivers_snapshot_and_live_bid_frames():
    from app.core.ratelimit import RateLimiter
    from app.main import create_app
    from app.realtime.bus import RealtimeBus
    from app.services.events import EventType
    from app.workers.outbox_relay import relay_once
    from starlette.testclient import TestClient

    app = create_app()
    app.state.redis = None
    app.state.bus = RealtimeBus(None)
    app.state.rate_limiter = RateLimiter(None)
    app.state.trust_proxy_headers = False
    app.state.background_tasks = []

    with TestClient(app) as client:
        fixture = client.portal.call(_bootstrap)
        auction_id = fixture["auction_id"]

        token = client.post(
            "/api/v1/auth/login",
            json={"email": fixture["email"], "password": fixture["password"]},
        ).json()["access_token"]

        with client.websocket_connect(f"/api/v1/auctions/{auction_id}/stream?token={token}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["event"] == "snapshot"
            assert snapshot["server_time"], "every frame must carry the server clock"
            assert snapshot["data"]["auction"]["id"] == auction_id
            assert snapshot["data"]["viewers"] >= 1

            ws.send_text(json.dumps({"type": "ping"}))

            placed = client.post(
                f"/api/v1/auctions/{auction_id}/bids",
                json={"max_amount": "70000"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert placed.status_code == 201, placed.text

            # The relay is what moves a committed outbox event onto the bus.
            client.portal.call(relay_once, app.state.bus)

            # After the snapshot the connection receives its own `presence`
            # frame, then the `pong`, then the bid. Read until the bid arrives
            # rather than assuming a fixed count — a blocking read of frames
            # that never come would hang the suite.
            frames = []
            for _ in range(6):
                frames.append(ws.receive_json())
                if frames[-1]["event"] == EventType.BID_PLACED:
                    break
            events = [f["event"] for f in frames]
            assert EventType.BID_PLACED in events, events
            assert "pong" in events, events

            payload = next(f for f in frames if f["event"] == EventType.BID_PLACED)["data"]
            assert payload["auction"]["current_price"] == "50000.00"
            assert payload["bids"][0]["bidder_alias"].startswith("Bidder-")
            assert "max_amount" not in json.dumps(payload), "private ceilings must not leak"
