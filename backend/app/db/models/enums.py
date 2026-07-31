from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    BUYER = "BUYER"
    ADMIN = "ADMIN"


class UserStatus(str, Enum):
    PENDING = "PENDING"  # registered, not yet KYC-verified
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class FuelType(str, Enum):
    PETROL = "PETROL"
    ELECTRIC = "ELECTRIC"
    HYBRID = "HYBRID"


class BikeStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"  # inspected, available to be auctioned
    IN_AUCTION = "IN_AUCTION"
    SOLD = "SOLD"
    WITHDRAWN = "WITHDRAWN"


class AuctionStatus(str, Enum):
    """Lifecycle.  Transitions are enforced in ``services/auction.py``.

    SCHEDULED --(starts_at)--> LIVE --(ends_at)--> ENDED --> SETTLED
         \\                      \\
          `--> CANCELLED          `--> CANCELLED
    """

    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    ENDED = "ENDED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class AuctionOutcome(str, Enum):
    PENDING = "PENDING"
    SOLD = "SOLD"
    RESERVE_NOT_MET = "RESERVE_NOT_MET"
    NO_BIDS = "NO_BIDS"
    CANCELLED = "CANCELLED"


class BidStatus(str, Enum):
    LEADING = "LEADING"
    OUTBID = "OUTBID"
    WON = "WON"
    LOST = "LOST"


class BidSource(str, Enum):
    MANUAL = "MANUAL"  # user explicitly submitted this amount
    PROXY = "PROXY"  # engine raised the price on the user's behalf


class HoldStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CAPTURED = "CAPTURED"


class DepositTxnType(str, Enum):
    TOPUP = "TOPUP"
    REFUND = "REFUND"
    HOLD = "HOLD"
    RELEASE = "RELEASE"
    CAPTURE = "CAPTURE"


class NotificationType(str, Enum):
    OUTBID = "OUTBID"
    AUCTION_STARTING = "AUCTION_STARTING"
    AUCTION_EXTENDED = "AUCTION_EXTENDED"
    AUCTION_WON = "AUCTION_WON"
    AUCTION_LOST = "AUCTION_LOST"
    RESERVE_NOT_MET = "RESERVE_NOT_MET"
