"""Tests for the HTTP endpoint.

The service is replaced with a double, so these tests cover what the route is
actually responsible for: request validation, the projection onto the sampled
response, error status mapping, and the guarantee that a token supplied in the
request body never comes back out.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.github_routes import get_ingestion_service
from app.core.exceptions import (
    AuthenticationError,
    BranchNotFoundError,
    EmbeddingError,
    IngestionError,
    RateLimitError,
    RepositoryNotFoundError,
    SourceUnavailableError,
)
from app.ingestion.ingestion_service import IngestionResult
from app.main import app
from app.models.code_chunk import CodeChunk
from app.models.ingest_response import SAMPLE_CHUNKS_LIMIT, SAMPLE_FILES_LIMIT
from app.models.repository_file import RepositoryFile

TOKEN = "ghp_secret_value_that_must_never_be_echoed"


def make_file(index: int) -> RepositoryFile:
    return RepositoryFile(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        path=f"src/file{index}.ts",
        file_name=f"file{index}.ts",
        extension=".ts",
        file_sha=f"sha{index}",
        size=100 + index,
        language="typescript",
        content="export const a = 1;\n",
    )


def make_chunk(
    index: int,
    content: str = "async login() { return true; }",
    *,
    embedded: bool = True,
) -> CodeChunk:
    return CodeChunk(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        file_path=f"src/file{index}.ts",
        file_sha=f"sha{index}",
        language="typescript",
        symbol_type="method",
        symbol_name="login",
        parent_symbol="AuthService",
        start_line=25,
        end_line=62,
        content=content,
        external_id=f"src/file{index}.ts",
        embedding=[float(index)] * 1536 if embedded else None,
        embedding_model="text-embedding-3-small" if embedded else None,
    )


def make_result(
    files: int = 3,
    chunks: int = 5,
    errors: list[tuple[str, str]] | None = None,
    *,
    embedded: bool = True,
) -> IngestionResult:
    return IngestionResult(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        discovered_files=240,
        accepted_files=files,
        parsed_files=files,
        truncated=False,
        files=[make_file(i) for i in range(files)],
        chunks=[make_chunk(i, embedded=embedded) for i in range(chunks)],
        errors=errors or [],
        embedded_chunks=chunks if embedded else 0,
        embedding_batches=(chunks + 29) // 30 if embedded else 0,
        embedding_model="text-embedding-3-small" if embedded else None,
        embedding_dimensions=1536 if embedded else None,
    )


class FakeService:
    """Stands in for GitHubIngestionService."""

    def __init__(self, result: IngestionResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def ingest(self, token, repository, branch=None, max_files=None, embed=True):
        # Record the unwrapped token so a test can prove it arrived intact -
        # the security guarantee is about the *response*, not the plumbing.
        self.calls.append(
            {
                "token": token.get_secret_value(),
                "repository": repository,
                "branch": branch,
                "max_files": max_files,
                "embed": embed,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


def client_with(service: FakeService) -> TestClient:
    app.dependency_overrides[get_ingestion_service] = lambda: service
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ------------------------------------------------------------------- health

def test_health() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------- happy path

def test_ingest_returns_the_funnel_counts() -> None:
    service = FakeService(make_result(files=3, chunks=5))
    response = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "branch": "main"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == "my-org/backend"
    assert body["branch"] == "main"
    assert body["commit_sha"] == "abc123"
    assert body["discovered_files"] == 240
    assert body["accepted_files"] == 3
    assert body["parsed_files"] == 3
    assert body["generated_chunks"] == 5


def test_request_reaches_the_service_intact() -> None:
    service = FakeService(make_result())
    client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "branch": "develop"},
    )

    assert service.calls == [
        {
            "token": TOKEN,
            "repository": "my-org/backend",
            "branch": "develop",
            "max_files": None,
            "embed": True,
        }
    ]


def test_branch_is_optional() -> None:
    service = FakeService(make_result())
    response = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    )

    assert response.status_code == 200
    assert service.calls[0]["branch"] is None


def test_chunks_carry_the_symbol_detail() -> None:
    service = FakeService(make_result(chunks=1))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    chunk = body["chunks"][0]
    assert chunk["file_path"] == "src/file0.ts"
    assert chunk["symbol_type"] == "method"
    assert chunk["symbol_name"] == "login"
    assert chunk["parent_symbol"] == "AuthService"
    assert chunk["start_line"] == 25
    assert chunk["end_line"] == 62
    assert chunk["content"] == "async login() { return true; }"


def test_errors_are_reported_per_file() -> None:
    service = FakeService(
        make_result(errors=[("src/broken.ts", "Skipped: file appears to be binary.")])
    )
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert body["errors"] == [
        {"file": "src/broken.ts", "reason": "Skipped: file appears to be binary."}
    ]


# ---------------------------------------------------------------- sampling

def test_response_samples_rather_than_returning_everything() -> None:
    """Counts stay complete; the file and chunk lists are capped."""
    service = FakeService(make_result(files=50, chunks=100))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert body["accepted_files"] == 50
    assert body["generated_chunks"] == 100
    assert len(body["resource_files"]) == SAMPLE_FILES_LIMIT
    assert len(body["chunks"]) == SAMPLE_CHUNKS_LIMIT


def test_chunk_content_is_never_truncated() -> None:
    """Sampling caps how many chunks come back, never what is inside one."""
    long_source = "x" * 1100
    result = make_result(chunks=0)
    result.chunks.append(make_chunk(0, content=long_source))

    body = client_with(FakeService(result)).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert body["chunks"][0]["content"] == long_source


def test_full_returns_every_file_and_chunk() -> None:
    """`full: true` serialises the entire result instead of a sample."""
    service = FakeService(make_result(files=50, chunks=100))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "full": True},
    ).json()

    assert len(body["resource_files"]) == 50
    assert len(body["chunks"]) == 100
    assert body["accepted_files"] == 50
    assert body["generated_chunks"] == 100


def test_full_leaves_chunk_content_whole_too() -> None:
    long_source = "x" * 1100
    result = make_result(chunks=0)
    result.chunks.append(make_chunk(0, content=long_source))

    body = client_with(FakeService(result)).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "full": True},
    ).json()

    assert body["chunks"][0]["content"] == long_source


def test_sampling_is_still_the_default() -> None:
    """Omitting `full` must not change the existing behaviour."""
    service = FakeService(make_result(files=50, chunks=100))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert len(body["resource_files"]) == SAMPLE_FILES_LIMIT
    assert len(body["chunks"]) == SAMPLE_CHUNKS_LIMIT


def test_max_files_override_is_passed_to_the_service() -> None:
    service = FakeService(make_result())
    client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "max_files": 1200},
    )

    assert service.calls[0]["max_files"] == 1200


def test_max_files_must_be_positive() -> None:
    response = TestClient(app).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "max_files": 0},
    )
    assert response.status_code == 422


def test_truncated_run_is_flagged() -> None:
    result = make_result()
    result.truncated = True
    body = client_with(FakeService(result)).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert body["truncated"] is True


# --------------------------------------------------------------- embedding

def test_counts_report_the_whole_funnel_including_embeddings() -> None:
    service = FakeService(make_result(files=3, chunks=95))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    assert body["counts"] == {
        "chunks": 95,
        "embeddings": 95,
        "embedding_batches": 4,  # ceil(95 / 30)
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "truncated_inputs": 0,
    }


def test_a_chunk_carries_its_whole_vector() -> None:
    """No preview, no flag to set - an embedded chunk arrives with its vector."""
    service = FakeService(make_result(chunks=1))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    ).json()

    chunk = body["chunks"][0]
    assert chunk["embedding"] == [0.0] * 1536
    assert chunk["embedding_model"] == "text-embedding-3-small"


def test_embed_false_is_passed_through_and_leaves_vectors_null() -> None:
    service = FakeService(make_result(chunks=2, embedded=False))
    body = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "embed": False},
    ).json()

    assert service.calls[0]["embed"] is False
    assert body["counts"]["embeddings"] == 0
    assert body["counts"]["embedding_batches"] == 0
    assert body["counts"]["embedding_model"] is None

    chunk = body["chunks"][0]
    assert chunk["embedding"] is None
    assert chunk["embedding_model"] is None


def test_an_embedding_failure_maps_to_502() -> None:
    service = FakeService(error=EmbeddingError())
    response = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    )

    assert response.status_code == 502
    assert TOKEN not in response.text


# ---------------------------------------------------------------- security

def test_the_token_never_appears_in_the_response() -> None:
    """The single most important guarantee of this endpoint."""
    service = FakeService(make_result(files=5, chunks=5))
    response = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend", "branch": "main"},
    )

    assert TOKEN not in response.text
    assert "ghp_" not in response.text


def test_the_token_never_appears_in_an_error_response() -> None:
    service = FakeService(error=AuthenticationError())
    response = client_with(service).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    )

    assert response.status_code == 401
    assert TOKEN not in response.text


def test_the_token_is_not_echoed_by_a_validation_error() -> None:
    """422 bodies repeat the offending input - the token must stay masked."""
    response = TestClient(app).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "not-a-valid-repo-spec"},
    )

    assert response.status_code == 422
    assert TOKEN not in response.text


def test_the_token_is_not_logged(caplog) -> None:
    service = FakeService(make_result())
    with caplog.at_level("DEBUG"):
        client_with(service).post(
            "/api/v1/github/ingest",
            json={"token": TOKEN, "repository": "my-org/backend"},
        )

    assert TOKEN not in caplog.text


# ------------------------------------------------------------ error mapping

@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthenticationError(), 401),
        (RepositoryNotFoundError(), 404),
        (BranchNotFoundError(), 404),
        (RateLimitError(), 429),
        (SourceUnavailableError(), 502),
        (IngestionError(), 500),
    ],
)
def test_pipeline_errors_map_to_http_statuses(
    error: IngestionError, status: int
) -> None:
    response = client_with(FakeService(error=error)).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    )

    assert response.status_code == status
    assert response.json() == {"detail": error.message}


def test_error_responses_do_not_leak_a_stack_trace() -> None:
    response = client_with(FakeService(error=SourceUnavailableError())).post(
        "/api/v1/github/ingest",
        json={"token": TOKEN, "repository": "my-org/backend"},
    )

    assert set(response.json()) == {"detail"}
    assert "Traceback" not in response.text


# -------------------------------------------------------------- validation

@pytest.mark.parametrize(
    "payload",
    [
        {"repository": "my-org/backend"},  # no token
        {"token": TOKEN},  # no repository
        {"token": TOKEN, "repository": "no-slash"},
        {"token": TOKEN, "repository": "https://github.com/my-org/backend"},
        {"token": TOKEN, "repository": "too/many/segments"},
    ],
)
def test_invalid_requests_are_rejected(payload: dict) -> None:
    assert TestClient(app).post("/api/v1/github/ingest", json=payload).status_code == 422


def test_openapi_documents_the_endpoint() -> None:
    """Swagger UI at /docs is the manual test path described in the README."""
    schema = TestClient(app).get("/openapi.json").json()
    assert "/api/v1/github/ingest" in schema["paths"]
