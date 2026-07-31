"""Idempotent demo seed: ``python -m app.seed``.

Creates an admin, a handful of verified buyers with funded deposits, a realistic
bike catalogue, and auctions spanning every lifecycle state — including one
ending in ~3 minutes so a reviewer can watch the anti-snipe extension fire
without waiting around.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.models.catalog import Bike
from app.db.models.enums import BikeStatus, DepositTxnType, FuelType, UserRole
from app.db.models.finance import DepositAccount, DepositTransaction
from app.db.models.user import User
from app.db.session import dispose_engine, session_scope
from app.services import auction as auction_service
from app.services import auth as auth_service

log = get_logger("seed")

CATALOGUE = [
    (
        "Royal Enfield",
        "Classic 350",
        "Redditch",
        2021,
        349,
        18_400,
        "Bengaluru",
        "A",
        91,
        152_000,
        "Chrome Red",
    ),
    (
        "Royal Enfield",
        "Himalayan",
        "Granite",
        2022,
        411,
        24_900,
        "Pune",
        "B",
        84,
        178_000,
        "Granite Black",
    ),
    (
        "Bajaj",
        "Pulsar NS200",
        "ABS",
        2020,
        199,
        31_200,
        "Hyderabad",
        "B",
        79,
        96_000,
        "Metallic Blue",
    ),
    ("KTM", "Duke 390", "BS6", 2022, 373, 12_800, "Mumbai", "A", 94, 232_000, "Electronic Orange"),
    ("Yamaha", "MT-15", "V2", 2023, 155, 7_400, "Chennai", "A", 96, 128_000, "Cyan Storm"),
    (
        "Honda",
        "CB350 H'ness",
        "DLX Pro",
        2021,
        348,
        21_600,
        "Delhi",
        "B",
        86,
        142_000,
        "Pearl Nightstar",
    ),
    (
        "TVS",
        "Apache RTR 200 4V",
        "Race Edition",
        2019,
        197,
        42_300,
        "Kolkata",
        "C",
        71,
        74_000,
        "Gloss Black",
    ),
    (
        "Suzuki",
        "Gixxer SF 250",
        "MotoGP",
        2022,
        249,
        15_100,
        "Ahmedabad",
        "A",
        90,
        156_000,
        "Blue White",
    ),
    (
        "Jawa",
        "42 Bobber",
        "Black Mirror",
        2023,
        334,
        6_200,
        "Jaipur",
        "A",
        93,
        186_000,
        "Mystique Black",
    ),
    (
        "Hero",
        "Xpulse 200 4V",
        "Rally Kit",
        2022,
        199,
        19_800,
        "Chandigarh",
        "B",
        82,
        118_000,
        "Matte Green",
    ),
    (
        "Kawasaki",
        "Ninja 300",
        "ABS",
        2019,
        296,
        28_700,
        "Bengaluru",
        "B",
        80,
        248_000,
        "Lime Green",
    ),
    ("Ather", "450X", "Gen 3", 2023, 0, 9_300, "Bengaluru", "A", 92, 118_000, "Space Grey"),
]

INSPECTION_SECTIONS = [
    "engine",
    "transmission",
    "brakes",
    "suspension",
    "electricals",
    "tyres",
    "chassis",
    "bodywork",
    "documents",
]


def inspection_report(score: int, rng: random.Random) -> dict:
    return {
        "overall_score": score,
        "inspected_at": datetime.now(timezone.utc).date().isoformat(),
        "inspector": "Vutto Certified Inspection",
        "sections": {
            s: {
                "score": max(50, min(100, score + rng.randint(-8, 8))),
                "notes": rng.choice(
                    [
                        "No issues found",
                        "Minor cosmetic wear",
                        "Serviced recently",
                        "Within tolerance",
                    ]
                ),
            }
            for s in INSPECTION_SECTIONS
        },
        "highlights": rng.sample(
            [
                "Single owner",
                "Full service history",
                "Insurance valid 8+ months",
                "Original paint",
                "Tyres above 60% tread",
                "No accident record",
            ],
            k=3,
        ),
    }


def image_set(make: str, model: str) -> list[str]:
    """Deterministic placeholder imagery keyed off the listing.

    Real deployments swap this for object-storage URLs; keeping it deterministic
    means the demo looks identical on every machine.
    """
    seed = f"{make}-{model}".lower().replace(" ", "-")
    return [f"https://picsum.photos/seed/{seed}-{i}/1200/800" for i in range(1, 6)]


async def seed() -> None:
    configure_logging(settings.log_level, settings.log_format)
    rng = random.Random(20240731)
    now = datetime.now(timezone.utc)

    async with session_scope() as session:
        existing = (await session.execute(select(func.count(User.id)))).scalar_one()
        if existing:
            log.info("seed.skipped", reason="database already has users", users=existing)
            return

        admin = await auth_service.register(
            session,
            email=settings.seed_admin_email,
            password=settings.seed_admin_password,
            full_name="Platform Operator",
            role=UserRole.ADMIN,
        )

        buyers: list[User] = []
        for name, email in [
            ("Aarav Sharma", "aarav@vutto.example.com"),
            ("Diya Nair", "diya@vutto.example.com"),
            ("Rohan Mehta", "rohan@vutto.example.com"),
            ("Ishita Rao", "ishita@vutto.example.com"),
            ("Kabir Singh", "kabir@vutto.example.com"),
        ]:
            user = await auth_service.register(
                session,
                email=email,
                password=settings.seed_demo_password,
                full_name=name,
            )
            buyers.append(user)
        await session.flush()

        # Fund every demo buyer so the deposit gate is satisfied out of the box.
        for user in buyers:
            account = (
                await session.execute(
                    select(DepositAccount).where(DepositAccount.user_id == user.id)
                )
            ).scalar_one()
            account.balance = Decimal("50000.00")
            session.add(
                DepositTransaction(
                    user_id=user.id,
                    type=DepositTxnType.TOPUP,
                    amount=Decimal("50000.00"),
                    reference="seed",
                    created_at=now,
                )
            )

        bikes: list[Bike] = []
        for i, (make, model, variant, year, cc, km, city, grade, score, value, colour) in enumerate(
            CATALOGUE
        ):
            bike = Bike(
                registration_number=f"KA{51 + i:02d}AB{1000 + i * 137:04d}",
                make=make,
                model=model,
                variant=variant,
                year=year,
                engine_cc=cc or 1,
                odometer_km=km,
                fuel_type=FuelType.ELECTRIC if cc == 0 else FuelType.PETROL,
                colour=colour,
                owners_count=rng.randint(1, 2),
                city=city,
                condition_grade=grade,
                inspection_score=score,
                inspection=inspection_report(score, rng),
                images=image_set(make, model),
                description=(
                    f"{year} {make} {model} {variant}. Inspected across "
                    f"{len(INSPECTION_SECTIONS)} sections and graded {grade}. "
                    f"Odometer reading {km:,} km."
                ),
                estimated_value=Decimal(value),
                status=BikeStatus.READY,
            )
            session.add(bike)
            bikes.append(bike)
        await session.flush()

        # A spread of lifecycle states, plus one closing very soon.
        plans = [
            ("live", timedelta(hours=-6), timedelta(hours=18)),
            ("live", timedelta(hours=-2), timedelta(hours=4)),
            ("live", timedelta(hours=-1), timedelta(minutes=45)),
            ("live", timedelta(minutes=-30), timedelta(minutes=3)),  # anti-snipe demo
            ("live", timedelta(minutes=-10), timedelta(hours=2)),
            ("live", timedelta(hours=-3), timedelta(hours=9)),
            ("scheduled", timedelta(hours=2), timedelta(hours=26)),
            ("scheduled", timedelta(hours=6), timedelta(hours=54)),
            ("scheduled", timedelta(days=1), timedelta(days=3)),
            ("scheduled", timedelta(days=2), timedelta(days=4)),
            ("live", timedelta(hours=-4), timedelta(hours=12)),
            ("live", timedelta(hours=-5), timedelta(hours=6)),
        ]

        for bike, (_kind, start_delta, end_delta) in zip(bikes, plans, strict=False):
            base = (bike.estimated_value * Decimal("0.72")).quantize(Decimal("1"))
            increment = Decimal(max(500, int(base / Decimal(120)) // 100 * 100))
            await auction_service.create_auction(
                session,
                bike_id=bike.id,
                starts_at=now + start_delta,
                ends_at=now + end_delta,
                start_price=base,
                bid_increment=increment,
                reserve_price=(bike.estimated_value * Decimal("0.88")).quantize(Decimal("1"))
                if rng.random() < 0.6
                else None,
                deposit_required=Decimal("5000.00"),
                anti_snipe_window_seconds=120,
                anti_snipe_extension_seconds=120,
                anti_snipe_max_extensions=20,
                notes=None,
                created_by=admin.id,
            )

        await auction_service.start_due_auctions(session, now=now)

    # Warm up a few auctions with real bids so the UI is not empty on first load.
    async with session_scope() as session:
        from app.db.models.auction import Auction
        from app.db.models.enums import AuctionStatus
        from app.services import bidding as bidding_service

        live = (
            (
                await session.execute(
                    select(Auction).where(Auction.status == AuctionStatus.LIVE).limit(6)
                )
            )
            .scalars()
            .all()
        )
        users = (
            (await session.execute(select(User).where(User.role == UserRole.BUYER))).scalars().all()
        )
        for auction in live:
            for bidder in rng.sample(users, k=rng.randint(2, 4)):
                target = auction.current_price + auction.bid_increment * rng.randint(1, 6)
                try:
                    await bidding_service.place_bid(
                        session,
                        auction_id=auction.id,
                        bidder=bidder,
                        max_amount=target,
                    )
                except Exception as exc:  # a losing bid here is realistic, not fatal
                    log.debug("seed.bid_skipped", error=str(exc))

    log.info(
        "seed.completed",
        admin=settings.seed_admin_email,
        buyers=5,
        bikes=len(CATALOGUE),
    )
    print(
        "\nSeed complete.\n"
        f"  Admin  : {settings.seed_admin_email} / {settings.seed_admin_password}\n"
        "  Buyers : aarav@vutto.example.com (also diya/rohan/ishita/kabir)\n"
        f"           password {settings.seed_demo_password}\n"
        "  Each buyer starts with a Rs 50,000 refundable deposit.\n"
        "  One auction closes in ~3 minutes so you can watch anti-snipe extend it.\n"
    )


async def _main() -> None:
    try:
        await seed()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
