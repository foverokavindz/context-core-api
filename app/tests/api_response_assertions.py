"""Assertions for tests that consume the shared HTTP response envelope."""

from datetime import datetime
from typing import Any

from httpx import Response


def _body(response: Response, *, success: bool) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {
        "success",
        "data",
        "message",
        "error",
        "timestamp",
    }
    assert body["success"] is success
    assert datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    return body


def response_data(response: Response) -> Any:
    body = _body(response, success=True)
    assert body["error"] is None
    return body["data"]


def response_error(response: Response) -> str:
    body = _body(response, success=False)
    assert body["data"] is None
    assert isinstance(body["error"], str)
    return body["error"]
