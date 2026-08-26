"""Tests for creating, reading, and querying chat sessions.

The whole feature is one service over two repositories and the retrieval
pipeline, so everything here runs over HTTP through the real app - real routing,
real Pydantic validation - with the database replaced by a fake session and the
pipeline by a fake that answers without a model. What the pipeline itself
decides is tested where each stage lives; what matters here is that the question
reaches it whole, that the answer and its sources come back, and that both turns
are stored. The last section drops to the objects themselves for the two rules
HTTP cannot show: that a repository never commits, and that the service does.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.dependencies import get_db
from app.entities import ChatSession, ChatSessionMessage, MessageRole, User
from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_type import ResourceType
from app.main import app
from app.tests.api_response_assertions import response_data, response_error
from app.models.chat.llm_config import DEFAULT_CHAT_MODEL
from app.models.chat.request import MAX_QUERY_LENGTH, SendQueryRequest
from app.models.chat.response import SNIPPET_CHARACTERS
from app.models.retrieval.answer import GeneratedAnswer
from app.models.retrieval.execution_result import (
    RetrievalExecutionResult,
    StepExecutionResult,
)
from app.models.retrieval.ontology.query_intent import QueryIntent
from app.models.retrieval.pipeline_result import RetrievalPipelineResult
from app.models.retrieval.prompt_analysis import PromptAnalysis
from app.models.retrieval.retrieval_plan import RetrievalPlan, RetrievalStep
from app.models.retrieval.retrieval_result import RetrievalResult
from app.repository.chat_message_repository import ChatMessageRepository
from app.repository.chat_session_repository import ChatSessionRepository
from app.services.chat_service import ChatService
from app.services.retrieval_service import get_retrieval_service

CHATS = "/api/v1/chats"

USER_ID = "33333333-3333-3333-3333-333333333333"
OTHER_USER_ID = "99999999-9999-9999-9999-999999999999"
TEAM_ID = "11111111-1111-1111-1111-111111111111"
DEPARTMENT_ID = "22222222-2222-2222-2222-222222222222"
HISTORY = f"/api/v1/users/{USER_ID}/chats"
CONVERSATIONS = f"/api/v1/users/{USER_ID}/conversations"

QUERY = "How does authentication work in this application?"
ANSWER = "Authentication is a JWT bearer token checked by AuthMiddleware [1]."
RESOLVED_QUERY = "How does authentication work in this application?"
PLAN_GOAL = "Understand how authentication is implemented."


# ------------------------------------------------------------------- fakes


class FakeScalarResult:

    def __init__(self, values: list) -> None:
        self.values = values

    def all(self) -> list:
        return self.values


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
        self.scalar_queries = 0

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

    def scalars(self, statement) -> FakeScalarResult:
        """Evaluate the one collection query used by the chat repository."""

        self.scalar_queries += 1
        parameters = statement.compile().params
        user_id = next(
            value for name, value in parameters.items() if "user_id" in name
        )
        chats = [
            row
            for row in self.added
            if isinstance(row, ChatSession) and row.user_id == user_id
        ]
        return FakeScalarResult(
            sorted(chats, key=lambda row: row.created_at, reverse=True)
        )


def retrieved(content: str, **overrides) -> RetrievalResult:
    """One chunk as a retriever hands it back."""
    fields = {
        "chunk_id": uuid4(),
        "content": content,
        "score": 0.12,
        "source": SourceType.GITHUB,
        "resource_type": ResourceType.GITHUB_FILE,
        "resource_title": "src/auth/middleware.ts",
        "external_id": "src/auth/middleware.ts",
    }
    return RetrievalResult(**{**fields, **overrides})


SOURCES = [
    retrieved("export class AuthMiddleware { verify(token) {} }"),
    retrieved(
        "Refresh tokens rotate on use.",
        source=SourceType.JIRA,
        resource_type=ResourceType.JIRA_ISSUE,
        resource_title="Add refresh token rotation",
        external_id="TRACK-25",
    ),
]


def pipeline_result(answer: str = ANSWER, sources=None) -> RetrievalPipelineResult:
    """A whole run: understood, planned, executed and answered."""
    return RetrievalPipelineResult(
        analysis=PromptAnalysis(
            resolved_query=RESOLVED_QUERY,
            intent=QueryIntent.IMPLEMENTATION_UNDERSTANDING,
            improved_query="authentication middleware JWT implementation",
        ),
        plan=RetrievalPlan(
            goal=PLAN_GOAL,
            steps=[
                RetrievalStep(
                    id="authentication_code",
                    source=SourceType.GITHUB,
                    goal="Find the authentication implementation",
                    query="authentication middleware JWT",
                )
            ],
        ),
        execution=RetrievalExecutionResult(
            steps=[
                StepExecutionResult(
                    step_id="authentication_code",
                    source=SourceType.GITHUB,
                    goal="Find the authentication implementation",
                    executed_query="authentication middleware JWT",
                    results=list(SOURCES if sources is None else sources),
                )
            ]
        ),
        answer=GeneratedAnswer(
            answer=answer, sources=list(SOURCES if sources is None else sources)
        ),
    )


class FakeRetrievalService:
    """Answers without a model, and records what it was asked to answer."""

    def __init__(self, result: RetrievalPipelineResult | None = None) -> None:
        self.result = result or pipeline_result()
        self.calls: list[tuple] = []

    def start(self, query: str, access, history=None) -> RetrievalPipelineResult:
        self.calls.append((query, access, history))
        return self.result


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


def historical_message(
    chat_session_id: UUID,
    role: MessageRole,
    content: str,
    created_at: datetime,
) -> ChatSessionMessage:
    return ChatSessionMessage(
        id=uuid4(),
        chat_session_id=chat_session_id,
        role=role,
        content=content,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.fixture
def session() -> FakeSession:
    """A session that already holds the user the requests name."""
    fake = FakeSession()
    fake.added.append(User(id=UUID(USER_ID)))
    return fake


@pytest.fixture
def retrieval() -> FakeRetrievalService:
    """The pipeline, faked. No chat model and no vector search are configured."""
    return FakeRetrievalService()


@pytest.fixture
def client(session, retrieval) -> TestClient:
    """A client whose requests get the fakes instead of a connection or a model."""
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_retrieval_service] = lambda: retrieval
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_retrieval_service, None)


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
    assert UUID(response_data(response)["chat_session_id"])
    assert len(chat_sessions(session)) == 1
    assert session.commits == 1


def test_the_created_session_carries_the_user_and_the_title(client, session) -> None:
    response = client.post(
        CHATS, json={"user_id": USER_ID, "title": "Authentication investigation"}
    )

    chat = chat_sessions(session)[0]
    assert str(chat.id) == response_data(response)["chat_session_id"]
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


# -------------------------------------------------------- get chat history


def test_chat_history_groups_and_orders_a_users_complete_history(
    client, session
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    older_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(USER_ID),
        title="Earlier investigation",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    first_message = historical_message(
        older_chat.id,
        MessageRole.USER,
        "What changed?",
        older_chat.created_at + timedelta(minutes=1),
    )
    second_message = historical_message(
        older_chat.id,
        MessageRole.ASSISTANT,
        "The authentication flow changed.",
        older_chat.created_at + timedelta(minutes=2),
    )
    # Deliberately attach them newest-first; the endpoint must normalize order.
    older_chat.messages = [second_message, first_message]

    recent_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(USER_ID),
        title=None,
        created_at=now,
        updated_at=now,
    )
    recent_chat.messages = []

    someone_elses_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(OTHER_USER_ID),
        title="Private",
        created_at=now + timedelta(days=1),
        updated_at=now + timedelta(days=1),
    )
    someone_elses_chat.messages = []
    session.added.extend([older_chat, recent_chat, someone_elses_chat])

    response = client.get(HISTORY)

    assert response.status_code == 200
    history = response_data(response)
    assert [item["chat_session_id"] for item in history] == [
        str(recent_chat.id),
        str(older_chat.id),
    ]
    assert history[0] == {
        "chat_session_id": str(recent_chat.id),
        "title": None,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "messages": [],
    }
    assert [item["message_id"] for item in history[1]["messages"]] == [
        str(first_message.id),
        str(second_message.id),
    ]
    assert history[1]["messages"][0] == {
        "message_id": str(first_message.id),
        "role": MessageRole.USER.value,
        "content": "What changed?",
        "created_at": first_message.created_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "updated_at": first_message.updated_at.isoformat().replace(
            "+00:00", "Z"
        ),
    }
    assert session.scalar_queries == 1
    assert session.commits == 0


def test_chat_history_is_not_limited_to_the_retrieval_context(
    client, session
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    chat = ChatSession(
        id=uuid4(),
        user_id=UUID(USER_ID),
        title="Long conversation",
        created_at=now,
        updated_at=now,
    )
    chat.messages = [
        historical_message(
            chat.id,
            MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            f"Message {index}",
            now + timedelta(minutes=index),
        )
        for index in range(8)
    ]
    session.added.append(chat)

    history = response_data(client.get(HISTORY))

    assert [message["content"] for message in history[0]["messages"]] == [
        f"Message {index}" for index in range(8)
    ]


def test_a_known_user_with_no_chat_history_gets_an_empty_list(client) -> None:
    response = client.get(HISTORY)

    assert response.status_code == 200
    assert response_data(response) == []


def test_an_unknown_users_chat_history_is_not_found(client, session) -> None:
    response = client.get(f"/api/v1/users/{OTHER_USER_ID}/chats")

    assert response.status_code == 404
    assert response_error(response) == "User not found."
    assert session.scalar_queries == 0
    assert session.commits == 0


def test_a_malformed_chat_history_user_id_is_rejected(client) -> None:
    response = client.get("/api/v1/users/not-a-uuid/chats")

    assert response.status_code == 422


# ------------------------------------------------------ list conversations


def test_conversation_list_returns_only_the_users_sessions_newest_first(
    client, session
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    older_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(USER_ID),
        title="Earlier investigation",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=12),
    )
    recent_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(USER_ID),
        title=None,
        created_at=now,
        updated_at=now,
    )
    someone_elses_chat = ChatSession(
        id=uuid4(),
        user_id=UUID(OTHER_USER_ID),
        title="Private",
        created_at=now + timedelta(days=1),
        updated_at=now + timedelta(days=1),
    )
    session.added.extend([older_chat, recent_chat, someone_elses_chat])

    response = client.get(CONVERSATIONS)

    assert response.status_code == 200
    assert response_data(response) == [
        {
            "chat_session_id": str(recent_chat.id),
            "title": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        },
        {
            "chat_session_id": str(older_chat.id),
            "title": "Earlier investigation",
            "created_at": older_chat.created_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "updated_at": older_chat.updated_at.isoformat().replace(
                "+00:00", "Z"
            ),
        },
    ]
    assert session.scalar_queries == 1
    assert session.commits == 0


def test_a_known_user_with_no_conversations_gets_an_empty_list(client) -> None:
    response = client.get(CONVERSATIONS)

    assert response.status_code == 200
    assert response_data(response) == []


def test_an_unknown_users_conversation_list_is_not_found(
    client, session
) -> None:
    response = client.get(f"/api/v1/users/{OTHER_USER_ID}/conversations")

    assert response.status_code == 404
    assert response_error(response) == "User not found."
    assert session.scalar_queries == 0
    assert session.commits == 0


def test_a_malformed_conversation_list_user_id_is_rejected(client) -> None:
    response = client.get("/api/v1/users/not-a-uuid/conversations")

    assert response.status_code == 422


# ------------------------------------------------------------- send a query


def test_a_query_is_answered(client, owned_chat) -> None:
    response = client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    assert response.status_code == 200
    body = response_data(response)
    assert body["chat_session_id"] == str(owned_chat.id)
    assert UUID(body["message_id"])
    assert UUID(body["answer_message_id"])
    assert body["status"] == "ANSWERED"
    assert body["answer"] == ANSWER


def test_the_question_and_the_answer_are_stored_as_two_messages(
    client, session, owned_chat
) -> None:
    response = client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    body = response_data(response)
    question, answer = messages(session)

    assert str(question.id) == body["message_id"]
    assert question.chat_session_id == owned_chat.id
    assert question.role is MessageRole.USER
    assert question.content == QUERY

    assert str(answer.id) == body["answer_message_id"]
    assert answer.chat_session_id == owned_chat.id
    assert answer.role is MessageRole.ASSISTANT
    assert answer.content == ANSWER

    # Committed separately, so a model failure cannot lose the question.
    assert session.commits == 2


def test_the_question_is_committed_before_the_pipeline_runs(
    session, owned_chat, retrieval
) -> None:
    """A model that fails takes the answer with it, and nothing else."""
    committed_when_asked: list[int] = []

    def remember(query, access, history=None):
        committed_when_asked.append(session.commits)
        raise RuntimeError("the model is down")

    retrieval.start = remember

    with pytest.raises(RuntimeError):
        ChatService(session, retrieval).send_query(
            owned_chat.id, SendQueryRequest(**query_payload())
        )

    assert committed_when_asked == [1]
    assert [row.role for row in messages(session)] == [MessageRole.USER]


def test_the_query_is_trimmed_before_it_is_stored(client, session, owned_chat) -> None:
    client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(query=f"  {QUERY}  ")
    )

    assert messages(session)[0].content == QUERY


# --------------------------------------------------- what reaches the pipeline


def test_the_pipeline_is_asked_the_question_as_it_was_stored(
    client, owned_chat, retrieval
) -> None:
    client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(query=f"  {QUERY}  ")
    )

    asked, _, _ = retrieval.calls[0]
    assert asked == QUERY


def test_the_pipeline_is_given_who_is_asking(client, owned_chat, retrieval) -> None:
    """Retrieval is filtered by these three, so all three must arrive."""
    client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    _, access, _ = retrieval.calls[0]
    assert str(access.user_id) == USER_ID
    assert str(access.team_id) == TEAM_ID
    assert str(access.department_id) == DEPARTMENT_ID


def test_a_first_question_carries_no_history(client, owned_chat, retrieval) -> None:
    client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    assert retrieval.calls[0][2] == []


def test_the_pipeline_is_asked_once_per_query(client, owned_chat, retrieval) -> None:
    client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())

    assert len(retrieval.calls) == 1


# ------------------------------------------------------- the answer's sources


def test_the_sources_come_back_in_the_order_the_answer_cites_them(
    client, owned_chat
) -> None:
    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    assert [source["chunk_id"] for source in body["sources"]] == [
        str(source.chunk_id) for source in SOURCES
    ]


def test_a_source_names_where_it_came_from_and_shows_what_it_said(
    client, owned_chat
) -> None:
    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    ticket = body["sources"][1]
    assert ticket["source"] == "JIRA"
    assert ticket["resource_type"] == "JIRA_ISSUE"
    assert ticket["external_id"] == "TRACK-25"
    assert ticket["resource_title"] == "Add refresh token rotation"
    assert ticket["snippet"] == "Refresh tokens rotate on use."
    assert ticket["score"] == 0.12


def test_a_long_source_is_shown_as_a_snippet_rather_than_whole(
    client, owned_chat, retrieval
) -> None:
    retrieval.result = pipeline_result(sources=[retrieved("x" * 5_000)])

    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    assert len(body["sources"][0]["snippet"]) == SNIPPET_CHARACTERS


def test_an_answer_from_nothing_carries_no_sources(
    client, owned_chat, retrieval
) -> None:
    retrieval.result = pipeline_result(
        answer="The retrieved sources do not say.", sources=[]
    )

    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    assert body["answer"] == "The retrieved sources do not say."
    assert body["sources"] == []


# ------------------------------------------------------- the retrieval trace


def test_the_trace_says_what_was_understood_and_planned(client, owned_chat) -> None:
    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    trace = body["retrieval"]
    assert trace["resolved_query"] == RESOLVED_QUERY
    assert trace["intent"] == QueryIntent.IMPLEMENTATION_UNDERSTANDING.value
    assert trace["retrieval_required"] is True
    assert trace["plan_goal"] == PLAN_GOAL


def test_the_trace_says_what_each_step_searched_and_found(client, owned_chat) -> None:
    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    step = body["retrieval"]["steps"][0]
    assert step["step_id"] == "authentication_code"
    assert step["source"] == "GITHUB"
    assert step["executed_query"] == "authentication middleware JWT"
    assert step["result_count"] == len(SOURCES)


def test_a_question_needing_no_retrieval_traces_no_plan(
    client, owned_chat, retrieval
) -> None:
    """Thanks, or an aside: answered, with nothing searched to answer it."""
    retrieval.result = RetrievalPipelineResult(
        analysis=PromptAnalysis(
            resolved_query="Thanks.",
            intent=QueryIntent.GENERAL_QUESTION,
            improved_query="thanks",
            retrieval_required=False,
        ),
        answer=GeneratedAnswer(answer="You are welcome."),
    )

    body = response_data(
        client.post(f"{CHATS}/{owned_chat.id}/query", json=query_payload())
    )

    assert body["answer"] == "You are welcome."
    assert body["sources"] == []
    assert body["retrieval"]["retrieval_required"] is False
    assert body["retrieval"]["plan_goal"] is None
    assert body["retrieval"]["steps"] == []


def test_an_unknown_chat_session_is_not_found(client, session, retrieval) -> None:
    response = client.post(f"{CHATS}/{uuid4()}/query", json=query_payload())

    assert response.status_code == 404
    assert messages(session) == []
    assert session.commits == 0
    assert retrieval.calls == []


def test_another_users_chat_session_is_reported_as_missing(
    client, session, retrieval
) -> None:
    """Not 403 - the response must not confirm that the id exists."""
    someone_elses = ChatSession(id=uuid4(), user_id=UUID(OTHER_USER_ID), title=None)
    session.added.append(someone_elses)

    response = client.post(f"{CHATS}/{someone_elses.id}/query", json=query_payload())

    assert response.status_code == 404
    assert messages(session) == []
    assert session.commits == 0
    assert retrieval.calls == []


@pytest.mark.parametrize("query", ["", "   ", "x" * (MAX_QUERY_LENGTH + 1)])
def test_an_unusable_query_is_rejected(
    client, session, owned_chat, retrieval, query: str
) -> None:
    response = client.post(
        f"{CHATS}/{owned_chat.id}/query", json=query_payload(query=query)
    )

    assert response.status_code == 422
    assert messages(session) == []
    assert retrieval.calls == []


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

    assert response.status_code == 200


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

    assert response.status_code == 200


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
    service = ChatService(session, FakeRetrievalService())

    with pytest.raises(Exception) as caught:
        service.send_query(owned_chat.id, SendQueryRequest(**query_payload()))

    assert caught.value.status_code == 500
    assert session.rollbacks == 1
    # The driver's own words never reach the client.
    assert "connection lost" not in caught.value.detail
