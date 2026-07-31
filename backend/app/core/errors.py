"""Domain error hierarchy and the RFC-7807-ish HTTP problem envelope.

Domain code raises semantic errors (``BidTooLowError``) and never knows about
HTTP.  A single exception handler maps them to a stable wire format:

    {"error": {"code": "BID_TOO_LOW", "message": "...", "details": {...}},
     "request_id": "..."}

The stable machine-readable ``code`` is what clients branch on — never the
human-readable message, which we are free to reword.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected (non-bug) failure."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --- 4xx: client / domain -------------------------------------------------
class ValidationError(AppError):
    code, status_code, message = "VALIDATION_ERROR", 422, "Request failed validation."


class AuthenticationError(AppError):
    code, status_code, message = "UNAUTHENTICATED", 401, "Authentication required."


class InvalidCredentialsError(AuthenticationError):
    code, message = "INVALID_CREDENTIALS", "Email or password is incorrect."


class TokenReuseError(AuthenticationError):
    code = "REFRESH_TOKEN_REUSED"
    message = "Refresh token reuse detected; all sessions have been revoked."


class PermissionDeniedError(AppError):
    code, status_code, message = "FORBIDDEN", 403, "You are not allowed to do that."


class NotFoundError(AppError):
    code, status_code, message = "NOT_FOUND", 404, "Resource not found."


class ConflictError(AppError):
    code, status_code, message = "CONFLICT", 409, "Conflicting state."


class RateLimitedError(AppError):
    code, status_code, message = "RATE_LIMITED", 429, "Too many requests. Slow down."


# --- Auction / bidding domain --------------------------------------------
class AuctionNotLiveError(ConflictError):
    code, message = "AUCTION_NOT_LIVE", "This auction is not accepting bids right now."


class AuctionEndedError(ConflictError):
    code, message = "AUCTION_ENDED", "This auction has already ended."


class BidTooLowError(ConflictError):
    code = "BID_TOO_LOW"
    message = "Your maximum bid is below the minimum acceptable amount."


class SelfOutbidError(ConflictError):
    code = "ALREADY_LEADING"
    message = "You are already the highest bidder; raise your maximum to increase it."


class SellerCannotBidError(PermissionDeniedError):
    code, message = "SELLER_CANNOT_BID", "You cannot bid on your own listing."


class InsufficientDepositError(ConflictError):
    code = "INSUFFICIENT_DEPOSIT"
    message = "Your refundable deposit does not cover this auction's requirement."


class AccountNotVerifiedError(PermissionDeniedError):
    code = "ACCOUNT_NOT_VERIFIED"
    message = "Your account must be verified before you can bid."


class IdempotencyConflictError(ConflictError):
    code = "IDEMPOTENCY_KEY_REUSED"
    message = "This idempotency key was already used with a different request body."
