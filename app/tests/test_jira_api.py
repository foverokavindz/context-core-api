"""Tests for the Jira HTTP endpoint.

The service is replaced with a double, so these tests cover what the route is
actually responsible for: request validation, the projection onto the sampled
response, error status mapping, and the guarantee that a token supplied in the
request body never comes back out.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.jira_routes import get_jira_ingestion_service
from app.core.exceptions import (
    EmbeddingError,
    IngestionError,
    JiraApiError,
    JiraAuthenticationError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitError,
)
from app.ingestion.jira_ingestion_service import JiraIngestionResult
from app.main import app
from app.models.jira_chunk import JiraChunk
from app.models.jira_issue import JiraIssue
from app.models.jira_request import JiraIngestRequest

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
TOKEN = "jira_secret_value_that_must_never_be_echoed"
PROJECT = "TRACK"

ENDPOINT = "/api/v1/jira/ingest"


def payload(**overrides) -> dict:
    body = {
        "site_url": SITE,
        "email": EMAIL,
        "api_token": TOKEN,
        "project_key": PROJECT,
    }
    body.update(overrides)
    return body


def make_issue(index: int, *, issue_type: str = "Story") -> JiraIssue:
    return JiraIssue(
        key=f"TRACK-{index}",
        project_key=PROJECT,
        issue_type=issue_type,
        summary=f"Summary {index}",
        description="A description.",
        status="In Progress",
        parent_key="TRACK-10" if issue_type == "Story" else None,
        child_issues=[] if issue_type == "Story" else ["TRACK-1"],
    )


def make_chunk(index: int, content: str = "Issue Key: TRACK-1") -> JiraChunk:
    return JiraChunk(
        key=f"TRACK-{index}",
        project_key=PROJECT,
        issue_type="Story",
        summary=f"Summary {index}",
        description="A description.",
        status="In Progress",
        parent_key="TRACK-10",
        content=content,
    )


def make_result(
    issues: int = 3, chunks: int = 3, errors: list[tuple[str, str]] | None = None,
    truncated: bool = False,
) -> JiraIngestionResult:
    return JiraIngestionResult(
        site_url=SITE,
        project_key=PROJECT,
        retrieved_issues=issues,
        truncated=truncated,
        issues=[make_issue(i) for i in range(issues)],
        chunks=[make_chunk(i) for i in range(chunks)],
        errors=errors or [],
    )


class FakeJiraService:
    """Stands in for JiraIngestionService."""

    def __init__(
        self,
        result: JiraIngestionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def ingest(
        self, site_url, email, api_token, project_key, max_issues=None, embed=True
    ) -> JiraIngestionResult:
        # Record the unwrapped token so a test can prove it arrived intact -
        # the security guarantee is about the *response*, not the plumbing.
        self.calls.append(
            {
                "site_url": site_url,
                "email": email,
                "token": api_token.get_secret_value(),
                "project_key": project_key,
                "max_issues": max_issues,
                "embed": embed,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else make_result()


def client_with(service: FakeJiraService) -> TestClient:
    app.dependency_overrides[get_jira_ingestion_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------- happy path


def test_ingest_returns_the_counts() -> None:
    result = JiraIngestionResult(
        site_url=SITE,
        project_key=PROJECT,
        retrieved_issues=3,
        issues=[make_issue(0, issue_type="Epic"), make_issue(1), make_issue(2)],
        chunks=[make_chunk(i) for i in range(3)],
    )

    body = client_with(FakeJiraService(result)).post(ENDPOINT, json=payload()).json()

    assert body["site_url"] == SITE
    assert body["project_key"] == PROJECT
    assert body["retrieved_issues"] == 3
    assert body["epics"] == 1
    assert body["stories"] == 2
    assert body["generated_chunks"] == 3
    assert body["truncated"] is False


def test_the_request_reaches_the_service_intact() -> None:
    service = FakeJiraService()

    client_with(service).post(ENDPOINT, json=payload(max_issues=42))

    assert service.calls == [
        {
            "site_url": SITE,
            "email": EMAIL,
            "token": TOKEN,
            "project_key": PROJECT,
            "max_issues": 42,
            "embed": True,
        }
    ]


def test_issues_carry_the_relationships() -> None:
    """The main reason to look at this response: did linking actually work."""
    body = client_with(FakeJiraService()).post(ENDPOINT, json=payload()).json()

    assert body["resource_files"][0]["key"] == "TRACK-0"
    assert body["resource_files"][0]["parent_key"] == "TRACK-10"
    assert body["resource_files"][0]["description"] == "A description."
    assert "child_issues" in body["resource_files"][0]


def test_chunks_carry_the_issue_detail() -> None:
    body = client_with(FakeJiraService()).post(ENDPOINT, json=payload()).json()

    sample = body["chunks"][0]
    assert sample["key"] == "TRACK-0"
    assert sample["issue_type"] == "Story"
    assert sample["parent_key"] == "TRACK-10"
    assert sample["content"].startswith("Issue Key:")


def test_errors_are_reported_per_issue() -> None:
    service = FakeJiraService(make_result(errors=[("issue #2", "no key")]))

    body = client_with(service).post(ENDPOINT, json=payload()).json()

    assert body["errors"] == [{"issue": "issue #2", "reason": "no key"}]


# ----------------------------------------------------------------- sampling


def test_the_response_samples_rather_than_returning_everything() -> None:
    """Counts stay complete; the issue and chunk lists are capped."""
    service = FakeJiraService(make_result(issues=40, chunks=40))

    body = client_with(service).post(ENDPOINT, json=payload()).json()

    assert body["retrieved_issues"] == 40
    assert body["generated_chunks"] == 40
    assert len(body["resource_files"]) == 10
    assert len(body["chunks"]) == 20


def test_full_returns_every_issue_and_chunk() -> None:
    service = FakeJiraService(make_result(issues=40, chunks=40))

    body = client_with(service).post(ENDPOINT, json=payload(full=True)).json()

    assert len(body["resource_files"]) == 40
    assert len(body["chunks"]) == 40


def test_long_chunk_content_is_never_shortened() -> None:
    """Sampling caps how many chunks come back, never what is inside one."""
    service = FakeJiraService(
        JiraIngestionResult(
            site_url=SITE, project_key=PROJECT, retrieved_issues=1,
            issues=[make_issue(0)], chunks=[make_chunk(0, content="x" * 2000)],
        )
    )

    body = client_with(service).post(ENDPOINT, json=payload()).json()

    assert body["chunks"][0]["content"] == "x" * 2000


def test_full_leaves_chunk_content_untruncated() -> None:
    service = FakeJiraService(
        JiraIngestionResult(
            site_url=SITE, project_key=PROJECT, retrieved_issues=1,
            issues=[make_issue(0)], chunks=[make_chunk(0, content="x" * 2000)],
        )
    )

    body = client_with(service).post(ENDPOINT, json=payload(full=True)).json()

    assert body["chunks"][0]["content"] == "x" * 2000


def test_a_truncated_run_is_flagged() -> None:
    service = FakeJiraService(make_result(truncated=True))

    body = client_with(service).post(ENDPOINT, json=payload()).json()

    assert body["truncated"] is True


def test_sampling_does_not_set_truncated() -> None:
    """A sampled response is a window onto a complete run, not a partial one."""
    service = FakeJiraService(make_result(issues=40, chunks=40))

    body = client_with(service).post(ENDPOINT, json=payload()).json()

    assert len(body["resource_files"]) == 10
    assert body["truncated"] is False


def test_max_issues_must_be_positive() -> None:
    response = TestClient(app).post(ENDPOINT, json=payload(max_issues=0))

    assert response.status_code == 422


# ----------------------------------------------------------------- security


def test_the_api_token_never_appears_in_the_response() -> None:
    """The single most important guarantee of this endpoint."""
    response = client_with(FakeJiraService()).post(ENDPOINT, json=payload())

    assert response.status_code == 200
    assert TOKEN not in response.text
    assert "jira_secret" not in response.text


def test_the_api_token_never_appears_in_an_error_response() -> None:
    service = FakeJiraService(error=JiraAuthenticationError())

    response = client_with(service).post(ENDPOINT, json=payload())

    assert TOKEN not in response.text


def test_the_api_token_is_not_echoed_by_a_validation_error() -> None:
    """A 422 renders the input; a SecretStr must stay masked there too."""
    response = TestClient(app).post(ENDPOINT, json=payload(project_key="lowercase"))

    assert response.status_code == 422
    assert TOKEN not in response.text


def test_the_api_token_is_masked_in_every_serialisation() -> None:
    """The property the whole SecretStr choice rests on: a request model that
    finds its way into a log line or a dump carries no token."""
    request = JiraIngestRequest(**payload())

    assert TOKEN not in repr(request)
    assert TOKEN not in str(request.model_dump())
    assert TOKEN not in request.model_dump_json()
    assert request.api_token.get_secret_value() == TOKEN


def test_the_api_token_is_not_logged(caplog) -> None:
    with caplog.at_level("DEBUG"):
        client_with(FakeJiraService()).post(ENDPOINT, json=payload())

    assert TOKEN not in caplog.text


# ------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (JiraAuthenticationError(), 401),
        (JiraPermissionError(), 403),
        (JiraNotFoundError(), 404),
        (JiraRateLimitError(), 429),
        (JiraApiError(), 502),
        (IngestionError(), 500),
    ],
)
def test_pipeline_errors_map_to_http_statuses(
    error: IngestionError, status: int
) -> None:
    response = client_with(FakeJiraService(error=error)).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == status
    assert response.json() == {"detail": error.message}


def test_error_responses_do_not_leak_a_stack_trace() -> None:
    service = FakeJiraService(error=JiraApiError())

    response = client_with(service).post(ENDPOINT, json=payload())

    assert set(response.json()) == {"detail"}
    assert "Traceback" not in response.text


# -------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"email": EMAIL, "api_token": TOKEN, "project_key": PROJECT},  # no site
        {"site_url": SITE, "api_token": TOKEN, "project_key": PROJECT},  # no email
        {"site_url": SITE, "email": EMAIL, "project_key": PROJECT},  # no token
        {"site_url": SITE, "email": EMAIL, "api_token": TOKEN},  # no project
    ],
)
def test_a_missing_required_field_is_rejected(body: dict) -> None:
    assert TestClient(app).post(ENDPOINT, json=body).status_code == 422


@pytest.mark.parametrize(
    "site_url",
    [
        "http://example.atlassian.net",  # plaintext, and the token rides on it
        "https://example.atlassian.net/browse/TRACK-1",
        "https://example.atlassian.net:8080",
        "https://jira",
        "example.atlassian.net",
        "not-a-url",
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
        "https://example.atlassian.net?token=leaked",
        "https://example.atlassian.net#token=leaked",
    ],
)
def test_a_site_url_carrying_a_credential_is_rejected(site_url: str) -> None:
    """httpx logs the request URL at INFO. Nothing secret may live in one."""
    response = TestClient(app).post(ENDPOINT, json=payload(site_url=site_url))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "project_key", ["proj", "1AB", "P", "TOOLONGKEYXX", "AB-C", "AB C", ""]
)
def test_an_unusable_project_key_is_rejected(project_key: str) -> None:
    response = TestClient(app).post(ENDPOINT, json=payload(project_key=project_key))

    assert response.status_code == 422


@pytest.mark.parametrize("email", ["not-an-email", "missing@domain", "@example.com"])
def test_an_unusable_email_is_rejected(email: str) -> None:
    assert TestClient(app).post(ENDPOINT, json=payload(email=email)).status_code == 422


def test_a_caller_supplied_jql_is_rejected() -> None:
    """V1 builds the query itself. Silently ignoring a `jql` key would let a
    caller believe they had scoped the request when they had not."""
    response = TestClient(app).post(
        ENDPOINT, json=payload(jql='project = "OTHER"')
    )

    assert response.status_code == 422


def test_a_trailing_slash_in_the_site_url_is_normalised() -> None:
    service = FakeJiraService()

    client_with(service).post(ENDPOINT, json=payload(site_url=f"{SITE}/"))

    assert service.calls[0]["site_url"] == SITE


def test_openapi_documents_the_endpoint() -> None:
    """Swagger UI at /docs is the manual test path described in the README."""
    schema = TestClient(app).get("/openapi.json").json()

    assert ENDPOINT in schema["paths"]
    assert "/api/v1/github/ingest" in schema["paths"]


# --------------------------------------------------------------- embedding

EMBEDDING_MODEL = "text-embedding-3-small"


def embedded(index: int):
    """One chunk as the embedding service leaves it."""
    return make_chunk(index).model_copy(
        update={
            "embedding": [round(0.1 * position, 4) for position in range(1536)],
            "embedding_model": EMBEDDING_MODEL,
        }
    )


def embedded_result():
    """A completed run whose chunks all carry vectors."""
    result = make_result(issues=2, chunks=0)
    result.chunks = [embedded(0), embedded(1)]
    result.embedded_chunks = len(result.chunks)
    result.embedding_batches = 1
    result.embedding_model = EMBEDDING_MODEL
    result.embedding_dimensions = 1536
    return result


def test_counts_report_the_embedding_tally() -> None:
    body = client_with(FakeJiraService(embedded_result())).post(
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
    body = client_with(FakeJiraService(embedded_result())).post(
        ENDPOINT, json=payload()
    ).json()

    chunk = body["chunks"][0]
    assert len(chunk["embedding"]) == 1536
    assert chunk["embedding"][:8] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert chunk["embedding_model"] == EMBEDDING_MODEL


def test_embed_false_is_passed_through_and_leaves_vectors_null() -> None:
    service = FakeJiraService()

    body = client_with(service).post(ENDPOINT, json=payload(embed=False)).json()

    assert service.calls[0]["embed"] is False
    assert body["counts"]["embeddings"] == 0
    assert body["counts"]["embedding_batches"] == 0
    assert body["counts"]["embedding_model"] is None

    chunk = body["chunks"][0]
    assert chunk["embedding"] is None
    assert chunk["embedding_model"] is None


def test_an_embedding_failure_maps_to_502() -> None:
    response = client_with(FakeJiraService(error=EmbeddingError())).post(
        ENDPOINT, json=payload()
    )

    assert response.status_code == 502
    assert TOKEN not in response.text
