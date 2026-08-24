"""Shared HTTP response envelope used by every API endpoint."""

from datetime import datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


def _utc_timestamp() -> str:
    """Return an ISO 8601 UTC timestamp that is convenient for JavaScript."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ApiResponse(BaseModel, Generic[T]):
    """The stable response contract shared with the frontend."""

    success: bool
    data: T | None
    message: str | None
    error: str | None
    timestamp: str

    @classmethod
    def ok(cls, data: T, message: str | None = None) -> "ApiResponse[T]":
        return cls(
            success=True,
            data=data,
            message=message,
            error=None,
            timestamp=_utc_timestamp(),
        )

    @classmethod
    def failed(cls, error: str, message: str | None = None) -> "ApiResponse[T]":
        return cls(
            success=False,
            data=None,
            message=message,
            error=error,
            timestamp=_utc_timestamp(),
        )
