"""Tests for the demo seed.

The seed is not production code, but it *is* the first thing a reviewer runs.
A seed that produces accounts nobody can log in with makes the whole build look
broken, so it gets the same treatment as everything else.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.db.models.auction import Auction
from app.db.models.bidding import Bid
from app.db.models.enums import AuctionStatus, UserRole
from app.db.models.finance import DepositAccount
from app.db.models.user import User
from app.schemas.auth import validate_password
from app.seed import CATALOGUE, seed
from app.services.bidding import verify_ledger
from pydantic import TypeAdapter
from pydantic.networks import EmailStr
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio

_email = TypeAdapter(EmailStr)


async def test_seeded_credentials_actually_work(client, sessionmaker_):
    """Regression guard.

    The seed writes users through the service layer, which bypasses the Pydantic
    schema the login endpoint uses. That gap once let `@vutto.test` addresses
    into the database — a reserved TLD that `email-validator` rejects — so every
    demo account existed but could not sign in.
    """
    await seed()

    for email, password in [
        (settings.seed_admin_email, settings.seed_admin_password),
        ("aarav@vutto.example.com", settings.seed_demo_password),
    ]:
        _email.validate_python(email)  # must satisfy the login schema
        validate_password(password)  # must satisfy the password policy
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, f"{email}: {response.text}"


async def test_seed_produces_a_usable_demo(sessionmaker_):
    await seed()
    async with sessionmaker_() as s:
        assert (await s.execute(select(func.count(User.id)))).scalar_one() == 6
        assert (
            await s.execute(select(func.count(User.id)).where(User.role == UserRole.ADMIN))
        ).scalar_one() == 1

        auctions = (await s.execute(select(func.count(Auction.id)))).scalar_one()
        assert auctions == len(CATALOGUE)

        live = (
            await s.execute(
                select(func.count(Auction.id)).where(Auction.status == AuctionStatus.LIVE)
            )
        ).scalar_one()
        assert live > 0, "a reviewer must land on something they can bid on"

        assert (await s.execute(select(func.count(Bid.id)))).scalar_one() > 0

        # Every buyer is funded, so the deposit gate never blocks the demo.
        for account in (await s.execute(select(DepositAccount))).scalars().all():
            assert account.held <= account.balance


async def test_seed_is_idempotent(sessionmaker_):
    """Re-running must not duplicate data — `make up` re-runs it on every boot."""
    await seed()
    await seed()
    async with sessionmaker_() as s:
        assert (await s.execute(select(func.count(User.id)))).scalar_one() == 6


async def test_every_seeded_ledger_verifies(sessionmaker_):
    await seed()
    async with sessionmaker_() as s:
        for auction_id in (await s.execute(select(Auction.id))).scalars().all():
            verdict = await verify_ledger(s, auction_id)
            assert verdict.valid, f"{auction_id}: {verdict.reason}"
