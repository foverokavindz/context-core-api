"""Contract tests for the response envelope shared with the frontend."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def assert_envelope(body: dict) -> None:
    assert set(body) == {
        "success",
        "data",
        "message",
        "error",
        "timestamp",
    }
    assert datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))


def test_success_response_uses_the_shared_envelope() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert_envelope(body)
    assert body == {
        "success": True,
        "data": {"status": "ok"},
        "message": None,
        "error": None,
        "timestamp": body["timestamp"],
    }


def test_http_error_uses_the_shared_envelope() -> None:
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert_envelope(body)
    assert body["success"] is False
    assert body["data"] is None
    assert body["message"] is None
    assert body["error"] == "Not Found"


def test_validation_error_uses_a_string_error() -> None:
    response = client.post("/api/v1/workspace", json={})

    assert response.status_code == 422
    body = response.json()
    assert_envelope(body)
    assert body["success"] is False
    assert body["data"] is None
    assert body["message"] is None
    assert isinstance(body["error"], str)
    assert "body.company_name" in body["error"]


def test_openapi_marks_every_envelope_field_as_required() -> None:
    schema = client.get("/openapi.json").json()
    health_schema = schema["paths"]["/health"]["get"]["responses"]["200"]
    reference = health_schema["content"]["application/json"]["schema"]["$ref"]
    component_name = reference.rsplit("/", 1)[-1]
    envelope_schema = schema["components"]["schemas"][component_name]

    assert set(envelope_schema["required"]) == {
        "success",
        "data",
        "message",
        "error",
        "timestamp",
    }
