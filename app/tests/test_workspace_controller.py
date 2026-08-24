from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.dependencies import get_db
from app.entities import Workspace
from app.main import app
from app.repository.workspace_repository import WorkspaceRepository
from app.tests.api_response_assertions import response_data, response_error

ENDPOINT = "/api/v1/workspace"


class FakeResult:
    def __init__(self, workspace: Workspace | None) -> None:
        self.workspace = workspace

    def scalar_one_or_none(self) -> Workspace | None:
        return self.workspace


class FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def execute(self, statement) -> FakeResult:
        workspace = next(
            (row for row in self.added if isinstance(row, Workspace)),
            None,
        )
        return FakeResult(workspace)

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def full_payload() -> dict[str, str]:
    return {
        "company_name": "Context Core",
        "subtitle": "Organizational Intelligence",
        "description": "Centralized organizational context.",
        "logo": "https://example.com/logo.png",
    }


def workspaces(session: FakeSession) -> list[Workspace]:
    return [row for row in session.added if isinstance(row, Workspace)]


def test_workspace_is_created_and_persisted(client, session) -> None:
    response = client.post(ENDPOINT, json=full_payload())

    assert response.status_code == 201
    body = response_data(response)
    assert UUID(body["id"])
    assert body == {"id": body["id"], **full_payload()}

    workspace = workspaces(session)[0]
    assert str(workspace.id) == body["id"]
    assert workspace.company_name == "Context Core"
    assert workspace.subtitle == "Organizational Intelligence"
    assert workspace.description == "Centralized organizational context."
    assert workspace.logo == "https://example.com/logo.png"
    assert session.commits == 1


def test_optional_fields_default_to_null(client, session) -> None:
    response = client.post(ENDPOINT, json={"company_name": "Context Core"})

    assert response.status_code == 201
    body = response_data(response)
    assert body["subtitle"] is None
    assert body["description"] is None
    assert body["logo"] is None

    workspace = workspaces(session)[0]
    assert workspace.subtitle is None
    assert workspace.description is None
    assert workspace.logo is None


def test_company_name_is_trimmed(client, session) -> None:
    response = client.post(ENDPOINT, json={"company_name": "  Context Core  "})

    assert response.status_code == 201
    assert response_data(response)["company_name"] == "Context Core"
    assert workspaces(session)[0].company_name == "Context Core"


def test_a_relative_logo_path_is_stored_as_sent(client, session) -> None:
    response = client.post(
        ENDPOINT,
        json={"company_name": "Context Core", "logo": "images/context-core.png"},
    )

    assert response.status_code == 201
    assert response_data(response)["logo"] == "images/context-core.png"
    assert workspaces(session)[0].logo == "images/context-core.png"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"company_name": ""},
        {"company_name": "   "},
        {"company_name": "Context Core", "unexpected": "field"},
    ],
)
def test_invalid_requests_are_rejected(client, session, body: dict) -> None:
    response = client.post(ENDPOINT, json=body)

    assert response.status_code == 422
    assert workspaces(session) == []
    assert session.commits == 0


def test_a_second_workspace_is_rejected(client, session) -> None:
    first = client.post(ENDPOINT, json={"company_name": "Context Core"})
    second = client.post(ENDPOINT, json={"company_name": "Another Company"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert response_error(second) == "Workspace has already been created."
    assert len(workspaces(session)) == 1
    assert session.commits == 1


def test_repository_flushes_but_does_not_commit(session) -> None:
    workspace = Workspace(id=uuid4(), company_name="Context Core")

    created = WorkspaceRepository(session).create(workspace)

    assert created is workspace
    assert session.flushes == 1
    assert session.commits == 0


def test_a_failed_creation_is_rolled_back(client, session, monkeypatch) -> None:
    def fail_to_flush() -> None:
        raise SQLAlchemyError("write failed")

    monkeypatch.setattr(session, "flush", fail_to_flush)

    response = client.post(ENDPOINT, json={"company_name": "Context Core"})

    assert response.status_code == 500
    assert response_error(response) == "The workspace could not be created."
    assert session.commits == 0
    assert session.rollbacks == 1
