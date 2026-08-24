"""Helpers for producing the API's shared success and failure envelopes."""

from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse

from app.models.common.api_response import ApiResponse


def error_response(
    error: str,
    *,
    status_code: int,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build a JSON failure response without exposing an internal exception."""
    body = ApiResponse[None].failed(error=error, message=message)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def error_text(detail: Any) -> str:
    """Coerce FastAPI's flexible HTTPException detail to the string contract."""
    if isinstance(detail, str):
        return detail
    return "Request failed."
