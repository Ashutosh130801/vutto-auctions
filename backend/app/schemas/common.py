from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str = Field(examples=["BID_TOO_LOW"])
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str | None = None


class Page(BaseModel, Generic[T]):
    """Cursor-free, count-bearing pagination.

    Offset pagination is the right call here: auction lists are small, users
    jump to arbitrary pages, and total counts drive the UI.  The bid ledger,
    which *is* unbounded and append-only, is paginated by ``sequence`` instead.
    """

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class Message(BaseModel):
    message: str
