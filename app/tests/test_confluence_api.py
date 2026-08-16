"""Tests for POST /api/v1/confluence/ingest.

The service is replaced with a double, so these tests cover what the route is
actually responsible for: validating the request, projecting a result onto the
response, sampling it, and mapping a pipeline error onto a status. Whether
ingestion itself works is the other modules' problem and is tested there.

The distinction this file exists to pin down is `truncated` versus `full`. One
says the ingestion did not see the whole space; the other says the response is
showing you part of what it did see. Confusing them would make a complete run
look partial, so there is an explicit regression test for it.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.confluence_routes import get_confluence_ingestion_service
from app.core.exceptions import (
    EmbeddingError,
    ConfluenceApiError,
    ConfluenceAuthenticationError,
    ConfluenceNotFoundError,
    ConfluencePermissionError,
    ConfluenceRateLimitError,
    IngestionError,
)
from app.ingestion.confluence_ingestion_service import ConfluenceIngestionResult
from app.main import app
from app.models.confluence_chunk import ConfluenceChunk
from app.models.confluence_page import ConfluencePage
from app.models.confluence_request import ConfluenceIngestRequest
from app.models.confluence_response import SAMPLE_CHUNKS_LIMIT, SAMPLE_PAGES_LIMIT

ENDPOINT = "/api/v1/confluence/ingest"

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
TOKEN = "confluence_secret_value_that_must_never_be_echoed"
SPACE = "TR"
SPACE_ID = "6422530"
SPACE_NAME = "TrackIt"


# ------------------------------------------------------------------- fakes


def payload(**overrides) -> dict:
    """A valid request body, with any field replaced or removed."""
    body = {
        "site_url": SITE,
        "email": EMAIL,
        "api_token": TOKEN,
        "space_key": SPACE,
    }
    body.update(overrides)
    return body


def make_page(page_id: str = "111", *, title: str = "Authentication") -> ConfluencePage:
    return ConfluencePage(
        page_id=page_id,
        space_id=SPACE_ID,
        space_key=SPACE,
        space_name=SPACE_NAME,
        title=title,
        parent_id=None,
        status="current",
        version_number=7,
        content="TrackIt uses JWT authentication.",
        external_id=page_id,
        version_key="7",
    )


def make_chunk(page_id: str = "111", *, content: str = "Space: TrackIt (TR)") -> ConfluenceChunk:
    return ConfluenceChunk(
        page_id=page_id,
        space_id=SPACE_ID,
        space_key=SPACE,
        space_name=SPACE_NAME,
        title="Authentication",
        parent_id=None,
        status="current",
        version_number=7,
        content=content,
    )


def make_result(
    *,
    pages: list[ConfluencePage] | None = None,
    chunks: list[ConfluenceChunk] | None = None,
    retrieved_pages: int | None = None,
    truncated: bool = False,
    errors: list[tuple[str, str]] | None = None,
) -> ConfluenceIngestionResult:
    pages = pages if pages is not None else [make_page()]
    chunks = chunks if chunks is not None else [make_chunk()]

    return ConfluenceIngestionResult(
        site_url=SITE,
        space_id=SPACE_ID,
        space_key=SPACE,
        space_name=SPACE_NAME,
        retrieved_pages=(
            retrieved_pages if retrieved_pages is not None else len(pages)
        ),
        truncated=truncated,
        pages=pages,
        chunks=chunks,
        errors=errors if errors is not None else [],
    )


class FakeConfluenceService:
    """Stands in for ConfluenceIngestionService."""

    def __init__(
        self,
        result: ConfluenceIngestionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def ingest(
        self, site_url, email, api_token, space_key, max_pages=None, embed=True
    ) -> ConfluenceIngestionResult:
        self.calls.append(
            {
                "site_url": site_url,
                "email": email,
                "token": api_token.get_secret_value(),
                "space_key": space_key,
                "max_pages": max_pages,
                "embed": embed,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else make_result()


def client_with(service: FakeConfluenceService) -> TestClient:
    app.dependency_overrides[get_confluence_ingestion_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------- happy path


def test_a_valid_request_succeeds() -> None:
    response = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload())

    assert response.status_code == 200


def test_the_counts_describe_the_funnel() -> None:
    result = make_result(
        pages=[make_page(str(n)) for n in range(3)],
        chunks=[make_chunk(str(n)) for n in range(3)],
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["retrieved_pages"] == 3
    assert body["parsed_pages"] == 3
    assert body["generated_chunks"] == 3


def test_the_resolved_space_is_echoed_back() -> None:
    body = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload()).json()

    assert body["space_key"] == SPACE
    assert body["space_id"] == SPACE_ID
    assert body["space_name"] == SPACE_NAME


def test_the_site_is_echoed_back() -> None:
    body = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload()).json()

    assert body["site_url"] == SITE


def test_the_pages_are_returned_in_full_shape() -> None:
    body = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload()).json()

    page = body["resource_files"][0]
    assert page["page_id"] == "111"
    assert page["space_key"] == SPACE
    assert page["title"] == "Authentication"
    assert page["version_number"] == 7
    assert page["content"] == "TrackIt uses JWT authentication."


def test_the_request_reaches_the_service_intact() -> None:
    service = FakeConfluenceService()

    client_with(service).post(ENDPOINT, json=payload(max_pages=42))

    assert service.calls == [
        {
            "site_url": SITE,
            "email": EMAIL,
            "token": TOKEN,
            "space_key": SPACE,
            "max_pages": 42,
            "embed": True,
        }
    ]


def test_a_trailing_slash_is_normalised_before_the_service_sees_it() -> None:
    service = FakeConfluenceService()

    client_with(service).post(ENDPOINT, json=payload(site_url=f"{SITE}/"))

    assert service.calls[0]["site_url"] == SITE


def test_an_empty_space_is_a_valid_response() -> None:
    result = make_result(pages=[], chunks=[])

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["retrieved_pages"] == 0
    assert body["generated_chunks"] == 0
    assert body["resource_files"] == []


def test_run_notes_are_reported() -> None:
    result = make_result(errors=[("page #2", "Confluence returned a page with no id.")])

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["errors"] == [
        {"page": "page #2", "reason": "Confluence returned a page with no id."}
    ]


# -------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"email": EMAIL, "api_token": TOKEN, "space_key": SPACE},  # no site
        {"site_url": SITE, "api_token": TOKEN, "space_key": SPACE},  # no email
        {"site_url": SITE, "email": EMAIL, "space_key": SPACE},  # no token
        {"site_url": SITE, "email": EMAIL, "api_token": TOKEN},  # no space
        {},
    ],
)
def test_a_missing_required_field_is_rejected(body: dict) -> None:
    assert TestClient(app).post(ENDPOINT, json=body).status_code == 422


@pytest.mark.parametrize(
    "site_url",
    [
        "http://example.atlassian.net",  # plaintext: the token rides in a header
        "https://example.atlassian.net/wiki/spaces/TR",  # a path, not a site
        "example.atlassian.net",  # no scheme
        "https://example",  # no TLD
        "https://example.atlassian.net:8443",  # a port
        "not a url at all",
        "",
    ],
)
def test_an_unusable_site_url_is_rejected(site_url: str) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload(site_url=site_url))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "site_url",
    [
        "https://user:pass@example.atlassian.net",
        "https://example.atlassian.net?token=abc",
        "https://example.atlassian.net#token=abc",
    ],
)
def test_a_site_url_carrying_a_credential_is_rejected(site_url: str) -> None:
    """Credentials belong in their own fields, where SecretStr can mask them."""
    assert TestClient(app).post(ENDPOINT, json=payload(site_url=site_url)).status_code == 422


@pytest.mark.parametrize(
    "space_key",
    [
        "",
        "TR/../PA",  # cannot be allowed to reshape the request path
        "TR PA",
        "TR?limit=1",
        "TR#frag",
        "TR&keys=PA",
        "a" * 256,
    ],
)
def test_an_unusable_space_key_is_rejected(space_key: str) -> None:
    assert TestClient(app).post(ENDPOINT, json=payload(space_key=space_key)).status_code == 422


@pytest.mark.parametrize("space_key", ["TR", "F", "CO", "trackit", "~6250f960ad6b7e00", "A_B-1.2"])
def test_a_real_space_key_shape_is_accepted(space_key: str) -> None:
    """Confluence keys are not Jira project keys - see SPACE_KEY_PATTERN."""
    response = client_with(FakeConfluenceService()).post(
        ENDPOINT, json=payload(space_key=space_key)
    )

    assert response.status_code == 200


@pytest.mark.parametrize("email", ["not-an-email", "@example.com", "a b@example.com"])
def test_an_unusable_email_is_rejected(email: str) -> None:
    assert TestClient(app).post(ENDPOINT, json=payload(email=email)).status_code == 422


@pytest.mark.parametrize("max_pages", [0, -1, -100])
def test_a_non_positive_page_cap_is_rejected(max_pages: int) -> None:
    assert TestClient(app).post(ENDPOINT, json=payload(max_pages=max_pages)).status_code == 422


def test_an_unknown_field_is_rejected() -> None:
    """Silently ignoring a `cql` key would be the wrong answer - see the model."""
    response = TestClient(app).post(ENDPOINT, json=payload(cql="space = PA"))

    assert response.status_code == 422


def test_a_caller_cannot_choose_the_body_format() -> None:
    response = TestClient(app).post(ENDPOINT, json=payload(body_format="atlas_doc_format"))

    assert response.status_code == 422


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ConfluenceAuthenticationError(), 401),
        (ConfluencePermissionError(), 403),
        (ConfluenceNotFoundError(), 404),
        (ConfluenceRateLimitError(), 429),
        (ConfluenceApiError(), 502),
        (IngestionError(), 500),
    ],
)
def test_pipeline_errors_map_to_http_statuses(
    error: IngestionError, status: int
) -> None:
    response = client_with(FakeConfluenceService(error=error)).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == status
    assert response.json() == {"detail": error.message}


def test_a_missing_space_is_reported_without_naming_why() -> None:
    """"Does not exist" and "not yours" are one case - see the connector."""
    error = ConfluenceNotFoundError(
        f"Confluence space '{SPACE}' does not exist or is not accessible."
    )

    response = client_with(FakeConfluenceService(error=error)).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == 404
    assert SPACE in response.json()["detail"]


def test_error_responses_do_not_leak_a_stack_trace() -> None:
    service = FakeConfluenceService(error=ConfluenceApiError())

    response = client_with(service).post(ENDPOINT, json=payload())

    assert set(response.json()) == {"detail"}
    assert "Traceback" not in response.text


# --------------------------------------------------------------- sampling


def test_the_page_list_is_sampled_by_default() -> None:
    result = make_result(
        pages=[make_page(str(n)) for n in range(40)],
        chunks=[make_chunk(str(n)) for n in range(40)],
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert len(body["resource_files"]) == SAMPLE_PAGES_LIMIT
    assert len(body["chunks"]) == SAMPLE_CHUNKS_LIMIT


def test_sampling_does_not_change_the_counts() -> None:
    """The whole space was processed; only the view of it is shortened."""
    result = make_result(
        pages=[make_page(str(n)) for n in range(40)],
        chunks=[make_chunk(str(n)) for n in range(40)],
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["retrieved_pages"] == 40
    assert body["parsed_pages"] == 40
    assert body["generated_chunks"] == 40


def test_sampling_does_not_set_truncated() -> None:
    """The regression test this file exists for."""
    result = make_result(
        pages=[make_page(str(n)) for n in range(40)],
        chunks=[make_chunk(str(n)) for n in range(40)],
        truncated=False,
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert len(body["resource_files"]) < body["parsed_pages"]
    assert body["truncated"] is False


def test_long_chunk_text_is_never_shortened() -> None:
    """Sampling caps how many chunks come back, never what is inside one."""
    content = "x" * 1100
    result = make_result(chunks=[make_chunk(content=content)])

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["chunks"][0]["content"] == content


def test_short_chunk_text_is_left_alone() -> None:
    result = make_result(chunks=[make_chunk(content="Space: TrackIt (TR)")])

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["chunks"][0]["content"] == "Space: TrackIt (TR)"


# ---------------------------------------------------------------- full=true


def test_full_returns_every_page_and_chunk() -> None:
    result = make_result(
        pages=[make_page(str(n)) for n in range(40)],
        chunks=[make_chunk(str(n)) for n in range(40)],
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert len(body["resource_files"]) == 40
    assert len(body["chunks"]) == 40


def test_full_leaves_chunk_text_untouched() -> None:
    content = "y" * 1100
    result = make_result(chunks=[make_chunk(content=content)])

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert body["chunks"][0]["content"] == content


def test_full_does_not_change_the_counts() -> None:
    result = make_result(
        pages=[make_page(str(n)) for n in range(40)],
        chunks=[make_chunk(str(n)) for n in range(40)],
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert body["generated_chunks"] == 40


def test_full_does_not_change_truncated() -> None:
    result = make_result(truncated=True)

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload(full=True)
    ).json()

    assert body["truncated"] is True


# -------------------------------------------------------------- truncation


def test_a_truncated_ingestion_says_so() -> None:
    result = make_result(truncated=True)

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload()
    ).json()

    assert body["truncated"] is True


def test_a_complete_ingestion_is_not_truncated() -> None:
    body = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload()).json()

    assert body["truncated"] is False


def test_a_capped_run_reports_what_it_actually_retrieved() -> None:
    result = make_result(
        pages=[make_page(str(n)) for n in range(3)],
        chunks=[make_chunk(str(n)) for n in range(3)],
        retrieved_pages=3,
        truncated=True,
    )

    body = client_with(FakeConfluenceService(result)).post(
        ENDPOINT, json=payload(max_pages=3)
    ).json()

    assert body["retrieved_pages"] == 3
    assert body["truncated"] is True


# ---------------------------------------------------------------- security


def test_the_api_token_never_comes_back_in_a_success_response() -> None:
    response = client_with(FakeConfluenceService()).post(ENDPOINT, json=payload())

    assert TOKEN not in response.text
    assert "confluence_secret" not in response.text


def test_the_api_token_never_comes_back_in_an_error_response() -> None:
    service = FakeConfluenceService(error=ConfluenceAuthenticationError())

    response = client_with(service).post(ENDPOINT, json=payload())

    assert TOKEN not in response.text


def test_the_api_token_never_comes_back_in_a_validation_response() -> None:
    """422 bodies echo the input, which is exactly where a token would surface."""
    response = TestClient(app).post(ENDPOINT, json=payload(space_key="TR/../PA"))

    assert response.status_code == 422
    assert TOKEN not in response.text


def test_the_api_token_is_masked_in_every_serialisation() -> None:
    request = ConfluenceIngestRequest(**payload())

    assert TOKEN not in repr(request)
    assert TOKEN not in str(request.model_dump())
    assert TOKEN not in request.model_dump_json()
    assert request.api_token.get_secret_value() == TOKEN


def test_the_api_token_never_reaches_the_logs(caplog) -> None:
    with caplog.at_level("DEBUG"):
        client_with(FakeConfluenceService()).post(ENDPOINT, json=payload())

    assert TOKEN not in caplog.text
    assert "confluence_secret" not in caplog.text


# ------------------------------------------------------------------ openapi


def test_openapi_documents_the_endpoint() -> None:
    """Swagger UI at /docs is the manual test path described in the README."""
    schema = TestClient(app).get("/openapi.json").json()

    assert ENDPOINT in schema["paths"]
    assert "/api/v1/jira/ingest" in schema["paths"]
    assert "/api/v1/github/ingest" in schema["paths"]


def test_the_endpoint_is_tagged_as_confluence() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["paths"][ENDPOINT]["post"]["tags"] == ["confluence"]


def test_adding_confluence_did_not_disturb_the_health_check() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------- embedding

EMBEDDING_MODEL = "text-embedding-3-small"


def embedded(page_id: str = "111"):
    """One chunk as the embedding service leaves it."""
    return make_chunk(page_id).model_copy(
        update={
            "embedding": [round(0.1 * position, 4) for position in range(1536)],
            "embedding_model": EMBEDDING_MODEL,
        }
    )


def embedded_result():
    """A completed run whose chunks all carry vectors."""
    result = make_result(chunks=[embedded("111"), embedded("222")])
    result.embedded_chunks = len(result.chunks)
    result.embedding_batches = 1
    result.embedding_model = EMBEDDING_MODEL
    result.embedding_dimensions = 1536
    return result


def test_counts_report_the_embedding_tally() -> None:
    body = client_with(FakeConfluenceService(embedded_result())).post(
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
    body = client_with(FakeConfluenceService(embedded_result())).post(
        ENDPOINT, json=payload()
    ).json()

    chunk = body["chunks"][0]
    assert len(chunk["embedding"]) == 1536
    assert chunk["embedding"][:8] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert chunk["embedding_model"] == EMBEDDING_MODEL


def test_embed_false_is_passed_through_and_leaves_vectors_null() -> None:
    service = FakeConfluenceService()

    body = client_with(service).post(ENDPOINT, json=payload(embed=False)).json()

    assert service.calls[0]["embed"] is False
    assert body["counts"]["embeddings"] == 0
    assert body["counts"]["embedding_batches"] == 0
    assert body["counts"]["embedding_model"] is None

    chunk = body["chunks"][0]
    assert chunk["embedding"] is None
    assert chunk["embedding_model"] is None


def test_an_embedding_failure_maps_to_502() -> None:
    response = client_with(FakeConfluenceService(error=EmbeddingError())).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == 502
    assert TOKEN not in response.text
