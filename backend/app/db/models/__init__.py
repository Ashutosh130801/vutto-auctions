"""Model registry.

Importing this package registers every mapper on ``Base.metadata`` — which is
what Alembic autogenerate and ``create_all`` in tests rely on.
"""

from app.db.base import Base
from app.db.models.auction import Auction
from app.db.models.bidding import Bid, Watchlist
from app.db.models.catalog import Bike
from app.db.models.enums import (
    AuctionOutcome,
    AuctionStatus,
    BidSource,
    BidStatus,
    BikeStatus,
    DepositTxnType,
    FuelType,
    HoldStatus,
    NotificationType,
    UserRole,
    UserStatus,
)
from app.db.models.finance import DepositAccount, DepositHold, DepositTransaction
from app.db.models.ops import AuditLog, IdempotencyRecord, Notification, OutboxEvent
from app.db.models.user import RefreshToken, User

__all__ = [
    "Auction",
    "AuctionOutcome",
    "AuctionStatus",
    "AuditLog",
    "Base",
    "Bid",
    "BidSource",
    "BidStatus",
    "Bike",
    "BikeStatus",
    "DepositAccount",
    "DepositHold",
    "DepositTransaction",
    "DepositTxnType",
    "FuelType",
    "HoldStatus",
    "IdempotencyRecord",
    "Notification",
    "NotificationType",
    "OutboxEvent",
    "RefreshToken",
    "User",
    "UserRole",
    "UserStatus",
    "Watchlist",
]
