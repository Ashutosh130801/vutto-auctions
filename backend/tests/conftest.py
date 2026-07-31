"""Test harness.

Tests run against a **real PostgreSQL** instance, never SQLite.  That is not
pedantry: the entire correctness argument of this project rests on
``SELECT ... FOR UPDATE``, ``SKIP LOCKED``, partial unique indexes and
``NUMERIC`` semantics, none of which SQLite reproduces.  A green suite on SQLite
would be actively misleading.

If ``TEST_DATABASE_URL`` is set (CI, with a Postgres service container) we use
it.  Otherwise we boot a throwaway PostgreSQL via ``pgserver`` so the suite is
runnable with a bare ``pytest`` and no infrastructure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("RUN_BACKGROUND_WORKERS", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("METRICS_ENABLED", "true")


def _provision_database() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    import contextlib

    import pgserver

    data_dir = os.getenv("PGSERVER_DIR", "/tmp/vutto-test-pg")
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    with contextlib.suppress(Exception):  # already exists on a re-run
        server.psql("CREATE DATABASE vutto_test;")
    uri = server.get_uri()
    host = uri.split("host=")[1]
    return f"postgresql+asyncpg://postgres@/vutto_test?host={host}"


DATABASE_URL = _provision_database()
os.environ["DATABASE_URL"] = DATABASE_URL


_SCHEMA_READY = False


@pytest_asyncio.fixture
async def engine() -> AsyncIterator:
    """A fresh engine per test, bound to that test's event loop.

    asyncpg connections are pinned to the loop that created them, so a
    session-scoped engine would explode the moment pytest-asyncio handed the
    next test a new loop.  Creating the engine per test costs microseconds (the
    pool fills lazily); the expensive part — the PostgreSQL instance and the
    schema — is created exactly once.
    """
    global _SCHEMA_READY
    from app.db import session as session_module
    from app.db.models import Base

    eng = create_async_engine(
        DATABASE_URL, pool_size=40, max_overflow=20, pool_pre_ping=False, future=True
    )
    if not _SCHEMA_READY:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        _SCHEMA_READY = True

    # Truncate rather than recreate: one statement, and it resets every table.
    async with eng.begin() as conn:
        tables = ", ".join(f'"{t}"' for t in Base.metadata.tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    session_module.configure(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker_(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(sessionmaker_) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    from app.core.ratelimit import RateLimiter
    from app.main import create_app
    from app.realtime.bus import RealtimeBus

    application = create_app()
    # Bypass lifespan: no Redis, no background workers, deterministic tests.
    application.state.redis = None
    application.state.bus = RealtimeBus(None)
    application.state.rate_limiter = RateLimiter(None)
    application.state.trust_proxy_headers = False
    application.state.background_tasks = []

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------
# Domain factories
# --------------------------------------------------------------------------
@pytest_asyncio.fixture
async def make_user(sessionmaker_):
    from app.db.models.enums import UserRole
    from app.db.models.finance import DepositAccount
    from app.services import auth as auth_service
    from sqlalchemy import select

    async def _make(
        email: str | None = None,
        *,
        password: str = "Password@123",
        role: UserRole = UserRole.BUYER,
        deposit: Decimal = Decimal("100000"),
        full_name: str = "Test Buyer",
    ):
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        async with sessionmaker_() as s:
            user = await auth_service.register(
                s, email=email, password=password, full_name=full_name, role=role
            )
            await s.flush()
            account = (
                await s.execute(select(DepositAccount).where(DepositAccount.user_id == user.id))
            ).scalar_one()
            account.balance = deposit
            await s.commit()
            await s.refresh(user)
            user.raw_password = password  # type: ignore[attr-defined]
            return user

    return _make


@pytest_asyncio.fixture
async def make_auction(sessionmaker_):
    from app.db.models.catalog import Bike
    from app.db.models.enums import BikeStatus
    from app.services import auction as auction_service

    async def _make(
        *,
        start_price: Decimal = Decimal("50000"),
        increment: Decimal = Decimal("1000"),
        reserve_price: Decimal | None = None,
        deposit_required: Decimal = Decimal("5000"),
        starts_in: timedelta = timedelta(minutes=-5),
        ends_in: timedelta = timedelta(hours=2),
        anti_snipe_window_seconds: int = 120,
        anti_snipe_extension_seconds: int = 120,
        anti_snipe_max_extensions: int = 20,
        live: bool = True,
    ):
        now = datetime.now(timezone.utc)
        async with sessionmaker_() as s:
            bike = Bike(
                registration_number=f"KA{uuid.uuid4().hex[:8].upper()}",
                make="Royal Enfield",
                model="Classic 350",
                year=2022,
                engine_cc=349,
                odometer_km=15000,
                city="Bengaluru",
                condition_grade="A",
                inspection_score=90,
                inspection={},
                images=["https://example.test/a.jpg"],
                estimated_value=Decimal("150000"),
                status=BikeStatus.READY,
            )
            s.add(bike)
            await s.flush()
            auction = await auction_service.create_auction(
                s,
                bike_id=bike.id,
                starts_at=now + starts_in,
                ends_at=now + ends_in,
                start_price=start_price,
                bid_increment=increment,
                reserve_price=reserve_price,
                deposit_required=deposit_required,
                anti_snipe_window_seconds=anti_snipe_window_seconds,
                anti_snipe_extension_seconds=anti_snipe_extension_seconds,
                anti_snipe_max_extensions=anti_snipe_max_extensions,
                notes=None,
                created_by=None,
            )
            if live:
                await auction_service.start_due_auctions(s, now=now)
            await s.commit()
            await s.refresh(auction)
            return auction

    return _make


@pytest_asyncio.fixture
async def auth_headers(client):
    async def _login(user) -> dict[str, str]:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": user.raw_password},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login
