"""Tests for POST /api/v1/ingestData/{external_source}.

Two halves, and they are tested separately because they run at different times.

The controller half is everything that happens before the response: resolving
the path segment, checking the body carries what its connector needs, building
the ExternalDataSource and scheduling the run. The background pipeline is
replaced with a double there, so these tests never touch a connector.

The pipeline half is what happens after. Those tests call
`run_ingestion_pipeline` directly with a fake per-source service and a temporary
runs directory, and check the two things the file is for: that permissions
reached every item and every chunk, and that the token did not reach the file.
"""

import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.background.pipeline import ingestion_pipeline
from app.background.pipeline.ingestion_pipeline import run_ingestion_pipeline
from app.core.exceptions import RepositoryNotFoundError
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.ingestion.ingestion_service import IngestionResult
from app.main import app
from app.models.code_chunk import CodeChunk
from app.models.ingest_data_request import IngestDataRequest
from app.models.repository_file import RepositoryFile
from app.services import ingestion_service

ENDPOINT = "/api/v1/ingestData"

TOKEN = "ghp-secret-value-that-must-never-be-echoed"
TEAM_ID = "11111111-1111-1111-1111-111111111111"
DEPARTMENT_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"

REPOSITORY = "my-org/backend"

# One valid config per source, so a test can name a source and get a body that
# would actually run.
CONFIGS = {
    "github": {"repository": REPOSITORY, "branch": "main"},
    "jira": {
        "site_url": "https://company.atlassian.net",
        "email": "bot@company.com",
        "project_key": "TRACK",
    },
    "confluence": {
        "site_url": "https://company.atlassian.net",
        "email": "bot@company.com",
        "space_key": "TR",
    },
    "slack": {"channel_id": "C0123456789"},
}


# ------------------------------------------------------------------- fakes


def payload(source: str = "github", **overrides) -> dict:
    """A valid request body for one source, with any field replaced."""
    body = {
        "title": "TrackIt API",
        "team_id": TEAM_ID,
        "department_id": DEPARTMENT_ID,
        "access_scope": "TEAM",
        "created_by_user_id": USER_ID,
        "source_type": source.upper(),
        "config": dict(CONFIGS[source]),
        "token": TOKEN,
    }
    body.update(overrides)
    return body


class SpyPipeline:
    """Stands in for run_ingestion_pipeline, recording what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, source, request) -> None:
        self.calls.append((source, request))


@pytest.fixture
def spy(monkeypatch) -> SpyPipeline:
    """Replace the background pipeline, so no test here opens a connection.

    Patched on the service module rather than on its own, because the service
    imported the name at import time and that binding is what add_task holds.
    """
    pipeline = SpyPipeline()
    monkeypatch.setattr(ingestion_service, "run_ingestion_pipeline", pipeline)
    return pipeline


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ------------------------------------------------------- the accepted cases


@pytest.mark.parametrize("source", ["github", "jira", "confluence", "slack"])
def test_every_source_is_accepted(client, spy, source: str) -> None:
    response = client.post(f"{ENDPOINT}/{source}", json=payload(source))

    assert response.status_code == 202
    body = response.json()
    assert body["source_type"] == source.upper()
    assert body["title"] == "TrackIt API"
    assert body["status"] == "PIPELINE_STARTED"
    # A parseable id rather than a fixed one - it is generated per request.
    assert UUID(body["external_data_source_id"])


def test_the_path_segment_is_case_insensitive(client, spy) -> None:
    response = client.post(f"{ENDPOINT}/GitHub", json=payload("github"))

    assert response.status_code == 202
    assert response.json()["source_type"] == "GITHUB"


def test_the_response_never_carries_the_token(client, spy) -> None:
    response = client.post(f"{ENDPOINT}/github", json=payload())

    assert TOKEN not in response.text


# ------------------------------------------------ what the service recorded


def test_the_external_data_source_is_built_from_the_request(client, spy) -> None:
    response = client.post(f"{ENDPOINT}/github", json=payload())

    source, _ = spy.calls[0]
    assert str(source.id) == response.json()["external_data_source_id"]
    assert source.name == "TrackIt API"
    assert source.source_type is SourceType.GITHUB
    assert source.status is SourceStatus.ACTIVE
    assert str(source.team_id) == TEAM_ID
    assert str(source.created_by_user_id) == USER_ID
    assert source.config == CONFIGS["github"]


def test_the_token_is_stored_on_the_source_unhashed(client, spy) -> None:
    """Deliberate for this phase - see docs/todo.md under Credentials."""
    client.post(f"{ENDPOINT}/github", json=payload())

    source, _ = spy.calls[0]
    assert source.token == TOKEN
    # The credential row this really belongs in is not written yet, and the
    # column stays null rather than pointing at something invented.
    assert source.credential_id is None


def test_the_pipeline_is_scheduled_once_per_request(client, spy) -> None:
    client.post(f"{ENDPOINT}/github", json=payload())
    client.post(f"{ENDPOINT}/slack", json=payload("slack"))

    assert len(spy.calls) == 2
    assert [source.source_type for source, _ in spy.calls] == [
        SourceType.GITHUB,
        SourceType.SLACK,
    ]


def test_the_permission_context_reaches_the_pipeline(client, spy) -> None:
    client.post(f"{ENDPOINT}/github", json=payload())

    _, request = spy.calls[0]
    assert str(request.team_id) == TEAM_ID
    assert str(request.department_id) == DEPARTMENT_ID
    assert request.access_scope is ResourceAccessScope.TEAM


# ------------------------------------------------------- the rejected cases


@pytest.mark.parametrize("segment", ["gitlab", "notion", "githubb", ""])
def test_an_unknown_source_is_a_404(client, spy, segment: str) -> None:
    response = client.post(f"{ENDPOINT}/{segment}", json=payload())

    assert response.status_code == 404
    assert not spy.calls


def test_the_404_names_what_is_supported(client, spy) -> None:
    detail = client.post(f"{ENDPOINT}/gitlab", json=payload()).json()["detail"]

    assert "gitlab" in detail
    for source in CONFIGS:
        assert source in detail


@pytest.mark.parametrize(
    "source,missing",
    [
        ("github", "repository"),
        ("jira", "project_key"),
        ("confluence", "space_key"),
        ("slack", "channel_id"),
    ],
)
def test_a_missing_config_key_is_a_400(client, spy, source: str, missing: str) -> None:
    config = dict(CONFIGS[source])
    del config[missing]

    response = client.post(f"{ENDPOINT}/{source}", json=payload(source, config=config))

    assert response.status_code == 400
    assert missing in response.json()["detail"]
    assert not spy.calls


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_config_value_is_a_400(client, spy, value: str) -> None:
    """A blank key fails the same way an absent one does.

    Left through, it would fail inside the connector instead, as a confusing
    404 about a repository nobody asked for.
    """
    response = client.post(
        f"{ENDPOINT}/github", json=payload(config={"repository": value})
    )

    assert response.status_code == 400
    assert "repository" in response.json()["detail"]
    assert not spy.calls


def test_a_source_type_that_contradicts_the_path_is_a_400(client, spy) -> None:
    response = client.post(f"{ENDPOINT}/github", json=payload("github", source_type="JIRA"))

    assert response.status_code == 400
    assert not spy.calls


def test_github_does_not_need_a_branch(client, spy) -> None:
    """It falls back to the repository default, which is the connector's job."""
    response = client.post(
        f"{ENDPOINT}/github", json=payload(config={"repository": REPOSITORY})
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"team_id": "not-a-uuid"},
        {"created_by_user_id": "not-a-uuid"},
        {"access_scope": "EVERYONE"},
        {"source_type": "GITLAB"},
        {"extra_field": "nope"},
    ],
)
def test_an_unusable_body_is_a_422(client, spy, overrides: dict) -> None:
    response = client.post(f"{ENDPOINT}/github", json=payload(**overrides))

    assert response.status_code == 422
    assert not spy.calls


@pytest.mark.parametrize("field", ["title", "team_id", "created_by_user_id", "token"])
def test_a_missing_required_field_is_a_422(client, spy, field: str) -> None:
    body = payload()
    del body[field]

    response = client.post(f"{ENDPOINT}/github", json=body)

    assert response.status_code == 422
    assert not spy.calls


# ------------------------------------------------------ the pipeline itself


def make_request(**overrides) -> IngestDataRequest:
    return IngestDataRequest(**payload(**overrides))


def make_source(request: IngestDataRequest):
    """The ExternalDataSource the service would have built for this request."""
    return ingestion_service._create_external_data_source(request, SourceType.GITHUB)


def make_github_result() -> IngestionResult:
    file = RepositoryFile(
        repository=REPOSITORY,
        branch="main",
        commit_sha="a" * 40,
        path="src/auth/auth.service.ts",
        file_name="auth.service.ts",
        extension=".ts",
        language="typescript",
        content="export class AuthService {}",
    )
    chunk = CodeChunk(
        repository=REPOSITORY,
        branch="main",
        commit_sha="a" * 40,
        file_path="src/auth/auth.service.ts",
        language="typescript",
        symbol_type="class",
        symbol_name="AuthService",
        start_line=1,
        end_line=1,
        content="export class AuthService {}",
    )
    return IngestionResult(
        repository=REPOSITORY,
        branch="main",
        commit_sha="a" * 40,
        discovered_files=1,
        accepted_files=1,
        parsed_files=1,
        files=[file],
        chunks=[chunk],
    )


class FakeGitHubService:
    """Stands in for GitHubIngestionService inside the pipeline."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def ingest(self, token, repository, branch=None, max_files=None, embed=True):
        if self.error is not None:
            raise self.error
        return make_github_result()


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    """Send run files somewhere disposable instead of app/data/runs."""
    monkeypatch.setattr(ingestion_pipeline, "RUNS_DIRECTORY", tmp_path)
    return tmp_path


def run_and_read(runs_dir, monkeypatch, *, error: Exception | None = None) -> dict:
    """Run one GitHub ingestion against a fake service and read its file."""
    monkeypatch.setattr(
        ingestion_pipeline,
        "GitHubIngestionService",
        lambda **kwargs: FakeGitHubService(error=error),
    )
    request = make_request()
    source = make_source(request)

    run_ingestion_pipeline(source, request)

    written = list(runs_dir.iterdir())
    assert len(written) == 1
    assert written[0].name == f"github_{source.id}.json"
    return json.loads(written[0].read_text(encoding="utf-8"))


def test_the_run_file_records_the_source(runs_dir, monkeypatch) -> None:
    run = run_and_read(runs_dir, monkeypatch)

    assert run["status"] == "COMPLETED"
    assert run["source"]["name"] == "TrackIt API"
    assert run["source"]["source_type"] == "GITHUB"
    assert run["source"]["team_id"] == TEAM_ID
    assert run["source"]["config"] == CONFIGS["github"]
    assert run["started_at"] and run["completed_at"]


def test_the_run_file_never_carries_the_token(runs_dir, monkeypatch) -> None:
    """The rule the rest of this codebase follows: no secret in a log, a
    response or a file."""
    run = run_and_read(runs_dir, monkeypatch)

    assert TOKEN not in json.dumps(run)
    assert "token" not in run["source"]


def test_every_resource_file_carries_the_permission_context(
    runs_dir, monkeypatch
) -> None:
    run = run_and_read(runs_dir, monkeypatch)

    resource_file = run["result"]["resource_files"][0]
    assert resource_file["team_id"] == TEAM_ID
    assert resource_file["department_id"] == DEPARTMENT_ID
    assert resource_file["access_scope"] == "TEAM"


def test_every_chunk_carries_the_permission_context(runs_dir, monkeypatch) -> None:
    """The copy that lets a vector search filter before it ranks."""
    run = run_and_read(runs_dir, monkeypatch)

    chunk = run["result"]["chunks"][0]
    assert chunk["team_id"] == TEAM_ID
    assert chunk["department_id"] == DEPARTMENT_ID
    assert chunk["access_scope"] == "TEAM"


def test_every_resource_file_names_the_source_it_came_from(
    runs_dir, monkeypatch
) -> None:
    """Half of the key a chunk uses to find its resource, and the half no
    connector can supply - the parser was handed a repository, not a source."""
    run = run_and_read(runs_dir, monkeypatch)

    source_id = run["source"]["external_data_source_id"]
    assert source_id

    for resource_file in run["result"]["resource_files"]:
        assert resource_file["external_data_source_id"] == source_id


def test_every_chunk_names_the_source_it_came_from(runs_dir, monkeypatch) -> None:
    """The other half arrives with the chunk itself; see docs/todo.md for the
    two columns the chunks table still has no source for."""
    run = run_and_read(runs_dir, monkeypatch)

    source_id = run["source"]["external_data_source_id"]

    for chunk in run["result"]["chunks"]:
        assert chunk["external_data_source_id"] == source_id


def test_a_failed_run_is_written_rather_than_lost(runs_dir, monkeypatch) -> None:
    """A background task has no client left to raise at."""
    run = run_and_read(runs_dir, monkeypatch, error=RepositoryNotFoundError())

    assert run["status"] == "FAILED"
    assert run["result"] is None
    assert run["error"]["type"] == "RepositoryNotFoundError"
    assert run["error"]["message"] == RepositoryNotFoundError.default_message


def test_a_failed_run_still_hides_the_token(runs_dir, monkeypatch) -> None:
    run = run_and_read(runs_dir, monkeypatch, error=RepositoryNotFoundError())

    assert TOKEN not in json.dumps(run)
