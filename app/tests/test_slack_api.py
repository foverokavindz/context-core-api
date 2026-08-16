"""Tests for POST /api/v1/slack/ingest.

The service is replaced with a double, so these tests cover what the route is
actually responsible for: validating the request, projecting a result onto the
response, sampling it, and mapping a pipeline error onto a status. Whether
ingestion itself works is the other modules' problem and is tested there.

The distinction this file exists to pin down is `truncated` versus `full`. One
says the ingestion did not see the whole channel; the other says the response is
showing you part of what it did see. Confusing them would make a complete run
look partial, so there is an explicit regression test for it.

There is a third thing that shortens what you see and must not be confused with
either, which is the parser's filter. That one shows up as `parsed_messages`
being lower than `retrieved_messages`, and it is normal - so the fixtures here
keep those two counts deliberately unequal rather than using matching numbers
that would hide a mix-up.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.slack_routes import get_slack_ingestion_service
from app.core.exceptions import (
    EmbeddingError,
    IngestionError,
    SlackApiError,
    SlackAuthenticationError,
    SlackNotFoundError,
    SlackPermissionError,
    SlackRateLimitError,
)
from app.ingestion.slack_ingestion_service import SlackIngestionResult
from app.main import app
from app.models.slack_chunk import SlackChunk
from app.models.slack_message import SlackMessage
from app.models.slack_request import SlackIngestRequest
from app.models.slack_response import SAMPLE_CHUNKS_LIMIT, SAMPLE_MESSAGES_LIMIT

ENDPOINT = "/api/v1/slack/ingest"

TOKEN = "xoxb-slack-secret-value-that-must-never-be-echoed"
CHANNEL = "C0123456789"
USER = "U0000000001"
TS = "1754810101.100100"


# ------------------------------------------------------------------- fakes


def payload(**overrides) -> dict:
    """A valid request body, with any field replaced or added."""
    body = {"token": TOKEN, "channel_id": CHANNEL}
    body.update(overrides)
    return body


def make_message(
    message_ts: str = TS,
    *,
    text: str = "We should update the authentication flow.",
) -> SlackMessage:
    return SlackMessage(
        channel_id=CHANNEL,
        message_ts=message_ts,
        author_id=USER,
        text=text,
        external_id=f"{CHANNEL}:{message_ts}",
    )


def make_chunk(
    message_ts: str = TS,
    *,
    content: str = "We should update the authentication flow.",
) -> SlackChunk:
    return SlackChunk(
        channel_id=CHANNEL, message_ts=message_ts, author_id=USER, content=content
    )


def make_result(
    *,
    messages: list[SlackMessage] | None = None,
    chunks: list[SlackChunk] | None = None,
    retrieved_messages: int | None = None,
    truncated: bool = False,
    errors: list[tuple[str, str]] | None = None,
) -> SlackIngestionResult:
    messages = messages if messages is not None else [make_message()]
    chunks = chunks if chunks is not None else [make_chunk()]

    return SlackIngestionResult(
        channel_id=CHANNEL,
        retrieved_messages=(
            retrieved_messages if retrieved_messages is not None else len(messages)
        ),
        truncated=truncated,
        messages=messages,
        chunks=chunks,
        errors=errors if errors is not None else [],
    )


class FakeSlackService:
    """Stands in for SlackIngestionService."""

    def __init__(
        self,
        result: SlackIngestionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def ingest(
        self, token, channel_id, max_messages=None, embed=True
    ) -> SlackIngestionResult:
        self.calls.append(
            {
                "token": token.get_secret_value(),
                "channel_id": channel_id,
                "max_messages": max_messages,
                "embed": embed,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else make_result()


def client_with(service: FakeSlackService) -> TestClient:
    app.dependency_overrides[get_slack_ingestion_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------- happy path


def test_a_valid_request_succeeds() -> None:
    assert client_with(FakeSlackService()).post(
        ENDPOINT, json=payload()
    ).status_code == 200


def test_the_channel_is_echoed_back() -> None:
    body = client_with(FakeSlackService()).post(ENDPOINT, json=payload()).json()

    assert body["channel_id"] == CHANNEL


def test_the_counts_describe_the_funnel() -> None:
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(3)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(3)],
        retrieved_messages=9,
    )

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["retrieved_messages"] == 9
    assert body["parsed_messages"] == 3
    assert body["generated_chunks"] == 3


def test_a_gap_between_retrieved_and_parsed_is_reported_not_hidden() -> None:
    """The filter dropping most of a channel's history is normal, not an error."""
    result = make_result(retrieved_messages=200, errors=[])

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["retrieved_messages"] == 200
    assert body["parsed_messages"] == 1
    assert body["truncated"] is False
    assert body["errors"] == []


def test_the_messages_carry_their_fields() -> None:
    body = client_with(FakeSlackService()).post(ENDPOINT, json=payload()).json()

    assert body["resource_files"][0] == {
        "channel_id": CHANNEL,
        "message_ts": TS,
        "author_id": USER,
        "text": "We should update the authentication flow.",
        # This endpoint carries no permission context - it takes a token and a
        # channel and nothing else - so the three fields serialise at their
        # defaults. Only /api/v1/ingestData fills them in.
        "team_id": None,
        "department_id": None,
        "access_scope": "TEAM",
        # Null for the same reason the three above are: this endpoint was given
        # a token and a channel, not a connected source.
        "external_data_source_id": None,
        # The resources columns. A message has no title and Slack reports no
        # version on what this connector reads, so two of the four are null.
        "external_id": f"{CHANNEL}:{TS}",
        "title": None,
        "version_key": None,
        "resource_type": "SLACK_MESSAGE",
    }


def test_the_chunks_carry_their_fields() -> None:
    body = client_with(FakeSlackService()).post(ENDPOINT, json=payload()).json()

    assert body["chunks"][0] == {
        "channel_id": CHANNEL,
        "message_ts": TS,
        "author_id": USER,
        "content": "We should update the authentication flow.",
        # This fixture's chunks were never embedded, so both vector fields read
        # as null rather than as an empty vector.
        "embedding": None,
        "embedding_model": None,
        # Unset for the same reason the message's are, one test up.
        "team_id": None,
        "department_id": None,
        "access_scope": "TEAM",
        "external_data_source_id": None,
    }


def test_an_empty_channel_still_answers_200() -> None:
    result = make_result(messages=[], chunks=[], retrieved_messages=0)

    response = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload())

    assert response.status_code == 200
    assert response.json()["resource_files"] == []
    assert response.json()["chunks"] == []


def test_errors_are_reported_as_subject_and_reason() -> None:
    result = make_result(
        errors=[(CHANNEL, "Ingestion stopped at the page ceiling.")]
    )

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["errors"] == [
        {"message": CHANNEL, "reason": "Ingestion stopped at the page ceiling."}
    ]


# ------------------------------------------------------------ reaching the service


def test_the_request_reaches_the_service_intact() -> None:
    service = FakeSlackService()

    client_with(service).post(ENDPOINT, json=payload(max_messages=42))

    assert service.calls == [
        {
            "token": TOKEN,
            "channel_id": CHANNEL,
            "max_messages": 42,
            "embed": True,
        }
    ]


def test_an_absent_cap_reaches_the_service_as_none() -> None:
    service = FakeSlackService()

    client_with(service).post(ENDPOINT, json=payload())

    assert service.calls[0]["max_messages"] is None


def test_full_is_not_passed_to_the_service() -> None:
    """It shortens the response, so it is the route's business, not the pipeline's."""
    service = FakeSlackService()

    client_with(service).post(ENDPOINT, json=payload(full=True))

    assert "full" not in service.calls[0]


# --------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"channel_id": CHANNEL},  # no token
        {"token": TOKEN},  # no channel
        {},
    ],
)
def test_a_missing_required_field_is_rejected(body: dict) -> None:
    assert TestClient(app).post(ENDPOINT, json=body).status_code == 422


@pytest.mark.parametrize(
    "channel_id",
    [
        "",
        "C",  # too short to be an id
        "#engineering",  # a name, not an id
        "C012345/../C999999",
        "C012345 6789",
        "C012345-6789",
        "https://example.slack.com/archives/C0123456789",
        "C" * 64,
    ],
)
def test_an_unusable_channel_id_is_rejected(channel_id: str) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload(channel_id=channel_id))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "channel_id",
    [
        "C0123456789",  # public channel
        "G0123456789",  # private channel
        "D0123456789",  # direct message
        "C01234567",  # the older, shorter form
        "C0123456789ABCDEFGH",  # enterprise-length
    ],
)
def test_a_plausible_channel_id_is_accepted(channel_id: str) -> None:
    """Slack has changed the shape of these before; validation stays wide."""
    service = FakeSlackService()

    response = client_with(service).post(
        ENDPOINT, json=payload(channel_id=channel_id)
    )

    assert response.status_code == 200


@pytest.mark.parametrize("max_messages", [0, -1, -100])
def test_a_non_positive_cap_is_rejected(max_messages: int) -> None:
    response = TestClient(app).post(
        ENDPOINT, json=payload(max_messages=max_messages)
    )

    assert response.status_code == 422


@pytest.mark.parametrize("max_messages", ["ten", 1.5, [], {}])
def test_a_cap_that_is_not_a_whole_number_is_rejected(max_messages: object) -> None:
    response = TestClient(app).post(
        ENDPOINT, json=payload(max_messages=max_messages)
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"oldest": "1754810101.000000"},
        {"latest": "1754899999.000000"},
        {"inclusive": True},
        {"thread_ts": TS},
        {"channels": ["C0123456789", "C9999999999"]},
        {"cursor": "dXNlcjpVMDYxTkZUVDI="},
    ],
)
def test_an_unknown_field_is_rejected(extra: dict) -> None:
    """The API accepts a token and one channel. Nothing may widen a run."""
    response = TestClient(app).post(ENDPOINT, json=payload(**extra))

    assert response.status_code == 422


def test_a_body_that_is_not_an_object_is_rejected() -> None:
    assert TestClient(app).post(ENDPOINT, json=[1, 2, 3]).status_code == 422


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SlackAuthenticationError(), 401),
        (SlackPermissionError(), 403),
        (SlackNotFoundError(), 404),
        (SlackRateLimitError(), 429),
        (SlackApiError(), 502),
        (IngestionError(), 500),
    ],
)
def test_pipeline_errors_map_to_http_statuses(
    error: IngestionError, status: int
) -> None:
    response = client_with(FakeSlackService(error=error)).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == status
    assert response.json() == {"detail": error.message}


def test_an_error_response_carries_no_partial_result() -> None:
    response = client_with(FakeSlackService(error=SlackRateLimitError())).post(
        ENDPOINT, json=payload()
    )

    assert set(response.json()) == {"detail"}


# ----------------------------------------------------------------- sampling


def test_the_message_list_is_sampled_by_default() -> None:
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(40)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(40)],
    )

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert len(body["resource_files"]) == SAMPLE_MESSAGES_LIMIT
    assert len(body["chunks"]) == SAMPLE_CHUNKS_LIMIT


def test_sampling_leaves_the_counts_alone() -> None:
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(40)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(40)],
    )

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["parsed_messages"] == 40
    assert body["generated_chunks"] == 40


def test_sampling_does_not_set_truncated() -> None:
    """The regression test this file exists for."""
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(40)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(40)],
        truncated=False,
    )

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert len(body["resource_files"]) < body["parsed_messages"]
    assert body["truncated"] is False


def test_truncated_survives_into_the_response() -> None:
    result = make_result(truncated=True)

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["truncated"] is True


def test_truncated_is_unaffected_by_full() -> None:
    result = make_result(truncated=True)

    body = client_with(FakeSlackService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert body["truncated"] is True


def test_long_chunk_text_is_never_shortened() -> None:
    """Sampling caps how many chunks come back, never what is inside one."""
    content = "x" * 1100
    result = make_result(chunks=[make_chunk(content=content)])

    body = client_with(FakeSlackService(result)).post(ENDPOINT, json=payload()).json()

    assert body["chunks"][0]["content"] == content


# --------------------------------------------------------------------- full


def test_full_returns_every_message_and_chunk() -> None:
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(40)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(40)],
    )

    body = client_with(FakeSlackService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert len(body["resource_files"]) == 40
    assert len(body["chunks"]) == 40


def test_full_leaves_chunk_text_untouched() -> None:
    content = "y" * 1100
    result = make_result(chunks=[make_chunk(content=content)])

    body = client_with(FakeSlackService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert body["chunks"][0]["content"] == content


def test_full_does_not_change_the_counts() -> None:
    result = make_result(
        messages=[make_message(f"10{n:02d}.000100") for n in range(40)],
        chunks=[make_chunk(f"10{n:02d}.000100") for n in range(40)],
        retrieved_messages=120,
    )

    sampled = client_with(FakeSlackService(result)).post(
        ENDPOINT, json=payload()
    ).json()
    complete = client_with(FakeSlackService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    for count in ("retrieved_messages", "parsed_messages", "generated_chunks"):
        assert sampled[count] == complete[count]


# ---------------------------------------------------------------- security


def test_the_token_never_comes_back_in_a_successful_response() -> None:
    response = client_with(FakeSlackService()).post(ENDPOINT, json=payload())

    assert TOKEN not in response.text


def test_the_token_never_comes_back_in_a_validation_response() -> None:
    """422 bodies echo the input, which is exactly where a token would surface."""
    response = TestClient(app).post(ENDPOINT, json=payload(channel_id="#nope"))

    assert response.status_code == 422
    assert TOKEN not in response.text


def test_the_token_never_comes_back_in_an_error_response() -> None:
    response = client_with(FakeSlackService(error=SlackAuthenticationError())).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == 401
    assert TOKEN not in response.text


def test_the_token_is_masked_in_every_serialisation() -> None:
    request = SlackIngestRequest(**payload())

    assert TOKEN not in repr(request)
    assert TOKEN not in str(request.model_dump())
    assert TOKEN not in request.model_dump_json()
    assert request.token.get_secret_value() == TOKEN


def test_the_token_never_reaches_the_logs(caplog) -> None:
    with caplog.at_level("DEBUG"):
        client_with(FakeSlackService()).post(ENDPOINT, json=payload())

    assert TOKEN not in caplog.text
    assert "xoxb-slack-secret" not in caplog.text


# ----------------------------------------------------------------- openapi


def test_openapi_documents_the_endpoint() -> None:
    """Swagger UI at /docs is the manual test path described in the README."""
    schema = TestClient(app).get("/openapi.json").json()

    assert ENDPOINT in schema["paths"]
    assert "/api/v1/confluence/ingest" in schema["paths"]
    assert "/api/v1/jira/ingest" in schema["paths"]
    assert "/api/v1/github/ingest" in schema["paths"]


def test_the_endpoint_is_tagged_as_slack() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"][ENDPOINT]["post"]["tags"] == ["slack"]


def test_adding_slack_did_not_disturb_the_health_check() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------- embedding

EMBEDDING_MODEL = "text-embedding-3-small"


def embedded(message_ts: str = TS):
    """One chunk as the embedding service leaves it."""
    return make_chunk(message_ts).model_copy(
        update={
            "embedding": [round(0.1 * position, 4) for position in range(1536)],
            "embedding_model": EMBEDDING_MODEL,
        }
    )


def embedded_result():
    """A completed run whose chunks all carry vectors."""
    result = make_result(chunks=[embedded("1000.000100"), embedded("1000.000200")])
    result.embedded_chunks = len(result.chunks)
    result.embedding_batches = 1
    result.embedding_model = EMBEDDING_MODEL
    result.embedding_dimensions = 1536
    return result


def test_counts_report_the_embedding_tally() -> None:
    body = client_with(FakeSlackService(embedded_result())).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["counts"] == {
        "chunks": 2,
        "embeddings": 2,
        "embedding_batches": 1,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": 1536,
        "truncated_inputs": 0,
    }


def test_a_chunk_carries_its_whole_vector() -> None:
    """No preview, no flag to set - an embedded chunk arrives with its vector."""
    body = client_with(FakeSlackService(embedded_result())).post(
        ENDPOINT, json=payload()
    ).json()

    chunk = body["chunks"][0]
    assert len(chunk["embedding"]) == 1536
    assert chunk["embedding"][:8] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert chunk["embedding_model"] == EMBEDDING_MODEL


def test_embed_false_is_passed_through_and_leaves_vectors_null() -> None:
    service = FakeSlackService()

    body = client_with(service).post(ENDPOINT, json=payload(embed=False)).json()

    assert service.calls[0]["embed"] is False
    assert body["counts"]["embeddings"] == 0
    assert body["counts"]["embedding_batches"] == 0
    assert body["counts"]["embedding_model"] is None

    chunk = body["chunks"][0]
    assert chunk["embedding"] is None
    assert chunk["embedding_model"] is None


def test_an_embedding_failure_maps_to_502() -> None:
    response = client_with(FakeSlackService(error=EmbeddingError())).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == 502
    assert TOKEN not in response.text
