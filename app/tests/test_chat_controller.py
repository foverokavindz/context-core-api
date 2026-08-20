"""Tests for POST /api/v1/chats and POST /api/v1/chats/{id}/query.

The whole feature is one service over two repositories, so everything here runs
over HTTP through the real app - real routing, real Pydantic validation - with
the database replaced by a fake session. The last section drops to the objects
themselves for the two rules HTTP cannot show: that a repository never commits,
and that the service does.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.dependencies import get_db
from app.entities import ChatSession, ChatSessionMessage, MessageRole, User
from app.main import app
from app.models.chat.llm_config import DEFAULT_CHAT_MODEL
from app.models.chat.request import MAX_QUERY_LENGTH, SendQueryRequest
from app.repository.chat_message_repository import ChatMessageRepository
from app.repository.chat_session_repository import ChatSessionRepository
from app.services.chat_service import ChatService

CHATS = "/api/v1/chats"

USER_ID = "33333333-3333-3333-3333-333333333333"
OTHER_USER_ID = "99999999-9999-9999-9999-999999999999"
TEAM_ID = "11111111-1111-1111-1111-111111111111"
DEPARTMENT_ID = "22222222-2222-2222-2222-222222222222"

QUERY = "How does authentication work in this application?"


# ------------------------------------------------------------------- fakes


class FakeSession:
    """A session that records rather than connects.

    Enough for this feature: the repositories only add, flush, commit and look
    a row up by primary key, and the ids are set in Python rather than by the
    database. `flushes` is counted so the no-commit rule can be asserted as
    "it flushed, and it did not commit" rather than only the second half.
    """

    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass

    def get(self, entity, ident):
        return next(
            (
                obj
                for obj in self.added
                if isinstance(obj, entity) and obj.id == ident
            ),
            None,
        )


def query_payload(**overrides) -> dict:
    """A valid send-query body, with any field replaced."""
    body = {
        "query": QUERY,
        "user_id": USER_ID,
        "team_id": TEAM_ID,
        "department_id": DEPARTMENT_ID,
    }
    body.update(overrides)
    return body


def messages(session: FakeSession) -> list[ChatSessionMessage]:
    return [row for row in session.added if isinstance(row, ChatSessionMessage)]


def chat_sessions(session: FakeSession) -> list[ChatSession]:
    return [row for row in session.added if isinstance(row, ChatSession)]


@pytest.fixture
def session() -> FakeSession:
    """A session that already holds the user the requests name."""
    fake = FakeSession()
    fake.added.append(User(id=UUID(USER_ID)))
    return fake


@pytest.fixture
def client(session) -> TestClient:
    """A client whose requests get the fake session instead of a connection."""
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def owned_chat(session) -> ChatSession:
    """A chat session belonging to USER_ID, already in the fake database."""
    chat = ChatSession(id=uuid4(), user_id=UUID(USER_ID), title=None)
    session.added.append(chat)
    return chat


# ----------------------------------------------------------- create a chat


def test_a_chat_session_is_created_for_a_known_user(client, session) -> None:
    response = client.post(CHATS, json={"user_id": USER_ID})

    assert response.status_code == 201
    assert UUID(response.json()["chat_session_id"])
    assert len(chat_sessions(session)) == 1
    assert session.commits == 1


def test_the_created_session_carries_the_user_and_the_title(client, session) -> None:
    response = client.post(
        CHATS, json={"user_id": USER_ID, "title": "Authentication investigation"}
    )

    chat = chat_sessions(session)[0]
    assert str(chat.id) == response.json()["chat_session_id"]
    assert str(chat.user_id) == USER_ID
    assert chat.title == "Authentication investigation"


def test_a_session_with_no_title_stores_null(client, session) -> None:
    client.post(CHATS, json={"user_id": USER_ID})

    assert chat_sessions(session)[0].title is None


def test_a_blank_title_is_rejected_rather_than_stored_empty(client) -> None:
    response = client.post(CHATS, json={"user_id": USER_ID, "title": "   "})

    assert response.status_code == 422


def test_an_unknown_user_cannot_open_a_chat(client, session) -> None:
    response = client.post(CHATS, json={"user_id": OTHER_USER_ID})

    assert response.status_code == 404
    assert chat_sessions(session) == []
    assert session.commits == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"user_id": "not-a-uuid"},
        {"user_id": USER_ID, "unexpected": "field"},
    ],
)
def test_a_malformed_create_body_is_rejected(client, body: dict) -> None:
    assert client.post(CHATS, json=body).status_code == 422


# ------------------------------------------------------------- send a query


def test_a_query_is_accepted_and_acknowledged(client, owned_chat) -> None:
    response = client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["chat_session_id"] == str(owned_chat.id)
    assert UUID(body["message_id"])
    assert body["status"] == "RECEIVED"


def test_the_query_is_stored_as_one_user_message(client, session, owned_chat) -> None:
    response = client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    stored = messages(session)
    assert len(stored) == 1
    assert str(stored[0].id) == response.json()["message_id"]
    assert stored[0].chat_session_id == owned_chat.id
    assert stored[0].role is MessageRole.USER
    assert stored[0].content == QUERY
    assert session.commits == 1


def test_no_assistant_message_is_created(client, session, owned_chat) -> None:
    """There is no answer yet, so nothing may write one."""
    client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    assert [row.role for row in messages(session)] == [MessageRole.USER]


def test_the_query_is_trimmed_before_it_is_stored(client, session, owned_chat) -> None:
    client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(query=f"  {QUERY}  ")
    )

    assert messages(session)[0].content == QUERY


def test_an_unknown_chat_session_is_not_found(client, session) -> None:
    response = client.post(f"{CHATS}/{uuid4()}/query", json=query_payload())

    assert response.status_code == 404
    assert messages(session) == []
    assert session.commits == 0


def test_another_users_chat_session_is_reported_as_missing(client, session) -> None:
    """Not 403 - the response must not confirm that the id exists."""
    someone_elses = ChatSession(id=uuid4(), user_id=UUID(OTHER_USER_ID), title=None)
    session.added.append(someone_elses)

    response = client.post(f"{CHATS}/{someone_elses.id}/query", json=query_payload())

    assert response.status_code == 404
    assert messages(session) == []
    assert session.commits == 0


@pytest.mark.parametrize("query", ["", "   ", "x" * (MAX_QUERY_LENGTH + 1)])
def test_an_unusable_query_is_rejected(client, session, owned_chat, query: str) -> None:
    response = client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(query=query)
    )

    assert response.status_code == 422
    assert messages(session) == []


@pytest.mark.parametrize(
    "body",
    [
        query_payload(user_id="not-a-uuid"),
        query_payload(team_id="not-a-uuid"),
        query_payload(department_id="not-a-uuid"),
        {"query": QUERY, "user_id": USER_ID},  # team_id and department_id missing
    ],
)
def test_a_malformed_query_body_is_rejected(client, owned_chat, body: dict) -> None:
    assert client.post(f"{CHATS}/{owned_chat.id}/query", json=body).status_code == 422


def test_a_query_at_the_length_limit_is_accepted(client, owned_chat) -> None:
    response = client.post(
        f"{CHATS}/{owned_chat.id}/query",
        json=query_payload(query="x" * MAX_QUERY_LENGTH),
    )

    assert response.status_code == 202


# --------------------------------------------------------------- llm config


def test_llm_config_defaults_when_it_is_omitted() -> None:
    request = SendQueryRequest(**query_payload())

    assert request.llm_config.model == DEFAULT_CHAT_MODEL
    assert request.llm_config.temperature == 0.2
    assert request.llm_config.max_tokens is None


def test_a_partial_llm_config_keeps_the_remaining_defaults() -> None:
    request = SendQueryRequest(**query_payload(llm_config={"temperature": 0.9}))

    assert request.llm_config.temperature == 0.9
    assert request.llm_config.model == DEFAULT_CHAT_MODEL


def test_the_endpoint_accepts_a_partial_llm_config(client, owned_chat) -> None:
    response = client.post(
        f"{CHATS}/{owned_chat.id}/query",
        json=query_payload(llm_config={"temperature": 0.9}),
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    "llm_config",
    [{"temperature": 2.5}, {"temperature": -0.1}, {"max_tokens": 0}],
)
def test_an_out_of_range_llm_config_is_rejected(
    client, owned_chat, llm_config: dict
) -> None:
    response = client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(llm_config=llm_config)
    )

    assert response.status_code == 422


# ---------------------------------------------- where the transaction lives


def test_the_chat_session_repository_does_not_commit() -> None:
    fake = FakeSession()

    ChatSessionRepository(fake).create(UUID(USER_ID), "A title")

    assert fake.flushes == 1
    assert fake.commits == 0


def test_the_chat_message_repository_does_not_commit() -> None:
    fake = FakeSession()

    ChatMessageRepository(fake).create(uuid4(), MessageRole.USER, QUERY)

    assert fake.flushes == 1
    assert fake.commits == 0


def test_the_service_rolls_back_when_the_database_fails(session, owned_chat) -> None:
    def explode() -> None:
        raise SQLAlchemyError("connection lost")

    session.commit = explode
    service = ChatService(session)

    with pytest.raises(Exception) as caught:
        service.send_query(owned_chat.id, SendQueryRequest(**query_payload()))

    assert caught.value.status_code == 500
    assert session.rollbacks == 1
    # The driver's own words never reach the client.
    assert "connection lost" not in caught.value.detail
