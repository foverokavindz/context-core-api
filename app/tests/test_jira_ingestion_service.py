"""Tests for the Jira ingestion service.

Only the network boundary is faked. The real parser, the real ADF flattener, the
real linking pass and the real chunker all run, so these cover the pipeline as a
whole rather than the orchestration in isolation.
"""

import pytest
from pydantic import SecretStr

from app.connectors.jira_connector import JiraSnapshot
from app.core.exceptions import (
    EmbeddingError,
    JiraAuthenticationError,
    JiraRateLimitError,
)
from app.ingestion.embedding_service import EmbeddingRun
from app.ingestion.jira_ingestion_service import JiraIngestionService

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
TOKEN = SecretStr("jira_fake_api_token_for_tests_only")
PROJECT = "TRACK"


# ------------------------------------------------------------------- fakes


def make_raw(
    key: str,
    *,
    issue_type: str = "Story",
    parent: str | None = None,
    description: object = "A description.",
    summary: str | None = None,
) -> dict:
    """One raw issue, shaped as the REST API returns it."""
    fields: dict = {
        "summary": summary or f"Summary for {key}",
        "description": description,
        "issuetype": {"name": issue_type},
        "status": {"name": "To Do"},
        "project": {"key": PROJECT},
    }
    if parent is not None:
        fields["parent"] = {"key": parent}
    return {"id": key, "key": key, "fields": fields}


def adf(text: str) -> dict:
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


class FakeJiraConnector:
    """Stands in for JiraConnector, recording how it was used."""

    def __init__(
        self,
        raw_issues: list[dict] | None = None,
        *,
        truncated: bool = False,
        errors: list[tuple[str, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.raw_issues = raw_issues or []
        self.truncated = truncated
        self.errors = errors or []
        self.error = error

        self.call_count = 0
        self.max_issues: int | None = None
        self.closed = False

    def get_issues(self, project_key: str, *, max_issues: int | None = None):
        self.call_count += 1
        self.max_issues = max_issues
        if self.error is not None:
            raise self.error
        return JiraSnapshot(
            site_url=SITE,
            project_key=project_key,
            retrieved_issues=len(self.raw_issues),
            truncated=self.truncated,
            issues=list(self.raw_issues),
            errors=list(self.errors),
        )

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeJiraConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def build_service(connector: FakeJiraConnector, **kwargs) -> JiraIngestionService:
    return JiraIngestionService(
        connector_factory=lambda site_url, email, api_token: connector, **kwargs
    )


def ingest(connector: FakeJiraConnector, **kwargs):
    return build_service(connector, **kwargs).ingest(SITE, EMAIL, TOKEN, PROJECT)


# --------------------------------------------------------------- happy path


def test_the_full_pipeline_produces_one_chunk_per_issue() -> None:
    """The invariant this version rests on."""
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-25", parent="TRACK-10"),
            make_raw("TRACK-26", parent="TRACK-10"),
        ]
    )

    result = ingest(connector)

    assert result.retrieved_issues == 3
    assert len(result.issues) == 3
    assert result.generated_chunks == 3


def test_epics_and_stories_are_counted() -> None:
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-11", issue_type="Epic"),
            make_raw("TRACK-25", parent="TRACK-10"),
        ]
    )

    result = ingest(connector)

    assert result.epics == 2
    assert result.stories == 1


def test_the_site_and_project_come_back_on_the_result() -> None:
    result = ingest(FakeJiraConnector([make_raw("TRACK-1")]))

    assert result.site_url == SITE
    assert result.project_key == PROJECT


def test_adf_descriptions_are_plain_text_by_the_time_they_are_chunked() -> None:
    """The whole reason the parser sits between the connector and the chunker."""
    connector = FakeJiraConnector(
        [make_raw("TRACK-25", description=adf("Employees can edit timesheets."))]
    )

    result = ingest(connector)

    assert result.issues[0].description == "Employees can edit timesheets."
    assert "Employees can edit timesheets." in result.chunks[0].content
    assert "type" not in result.chunks[0].content


# ----------------------------------------------------------------- linking


def test_a_story_is_linked_to_its_epic() -> None:
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-21", parent="TRACK-10"),
            make_raw("TRACK-25", parent="TRACK-10"),
        ]
    )

    issues = {issue.key: issue for issue in ingest(connector).issues}

    assert issues["TRACK-10"].child_issues == ["TRACK-21", "TRACK-25"]
    assert issues["TRACK-21"].parent_key == "TRACK-10"
    assert issues["TRACK-25"].parent_key == "TRACK-10"


def test_linking_costs_no_extra_connector_calls() -> None:
    """The reason `parent` is one of the six requested fields: children are
    resolved locally rather than with one request per epic."""
    connector = FakeJiraConnector(
        [make_raw("TRACK-10", issue_type="Epic")]
        + [make_raw(f"TRACK-{i}", parent="TRACK-10") for i in range(20, 40)]
    )

    ingest(connector)

    assert connector.call_count == 1


def test_children_are_sorted_deterministically() -> None:
    """The same project must always produce the same chunk text."""
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-28", parent="TRACK-10"),
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-21", parent="TRACK-10"),
        ]
    )

    issues = {issue.key: issue for issue in ingest(connector).issues}

    assert issues["TRACK-10"].child_issues == ["TRACK-21", "TRACK-28"]


def test_a_child_is_never_listed_twice() -> None:
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-25", parent="TRACK-10"),
            make_raw("TRACK-25", parent="TRACK-10"),
        ]
    )

    issues = [i for i in ingest(connector).issues if i.key == "TRACK-10"]

    assert issues[0].child_issues == ["TRACK-25"]


def test_an_epic_with_no_children_has_an_empty_child_list() -> None:
    result = ingest(FakeJiraConnector([make_raw("TRACK-10", issue_type="Epic")]))

    assert result.issues[0].child_issues == []


def test_an_orphan_parent_key_is_preserved() -> None:
    """A capped run leaves stories whose epic was never retrieved."""
    connector = FakeJiraConnector([make_raw("TRACK-25", parent="TRACK-999")])

    issue = ingest(connector).issues[0]

    assert issue.parent_key == "TRACK-999"
    assert "Parent Epic: TRACK-999" in ingest(connector).chunks[0].content


def test_orphans_are_summarised_in_one_error_entry() -> None:
    """One row per orphan would flood a truncated run's response."""
    connector = FakeJiraConnector(
        [make_raw(f"TRACK-{i}", parent="TRACK-999") for i in range(5)]
    )

    result = ingest(connector)

    orphan_errors = [reason for _, reason in result.errors if "parent" in reason]
    assert orphan_errors == [
        "5 issue(s) reference a parent that was not part of this ingestion."
    ]


def test_a_story_parented_to_a_story_is_treated_as_an_orphan() -> None:
    """Only an Epic collects children; a subtask's parent must not."""
    connector = FakeJiraConnector(
        [make_raw("TRACK-25"), make_raw("TRACK-26", parent="TRACK-25")]
    )

    issues = {issue.key: issue for issue in ingest(connector).issues}

    assert issues["TRACK-25"].child_issues == []
    assert issues["TRACK-26"].parent_key == "TRACK-25"


def test_a_self_referencing_parent_is_ignored() -> None:
    connector = FakeJiraConnector(
        [make_raw("TRACK-10", issue_type="Epic", parent="TRACK-10")]
    )

    assert ingest(connector).issues[0].child_issues == []


def test_no_orphan_note_when_everything_links() -> None:
    connector = FakeJiraConnector(
        [make_raw("TRACK-10", issue_type="Epic"), make_raw("TRACK-25", parent="TRACK-10")]
    )

    assert ingest(connector).errors == []


# ------------------------------------------------------------------- wiring


def test_the_issue_cap_is_passed_through() -> None:
    connector = FakeJiraConnector([])

    build_service(connector, max_issues=42).ingest(SITE, EMAIL, TOKEN, PROJECT)

    assert connector.max_issues == 42


def test_the_issue_cap_can_be_overridden_per_run() -> None:
    connector = FakeJiraConnector([])

    build_service(connector, max_issues=42).ingest(
        SITE, EMAIL, TOKEN, PROJECT, max_issues=7
    )

    assert connector.max_issues == 7


def test_the_connector_is_closed_once_fetching_is_done() -> None:
    """The session holding the caller's token does not outlive the fetch."""
    connector = FakeJiraConnector([make_raw("TRACK-1")])

    result = ingest(connector)

    assert connector.closed is True
    assert result.generated_chunks == 1


def test_truncation_is_carried_into_the_result() -> None:
    connector = FakeJiraConnector([make_raw("TRACK-1")], truncated=True)

    assert ingest(connector).truncated is True


def test_connector_errors_are_carried_into_the_result() -> None:
    connector = FakeJiraConnector(
        [make_raw("TRACK-1")], errors=[(PROJECT, "Ingestion stopped at the cap.")]
    )

    assert (PROJECT, "Ingestion stopped at the cap.") in ingest(connector).errors


@pytest.mark.parametrize(
    "error", [JiraAuthenticationError(), JiraRateLimitError()]
)
def test_whole_run_failures_propagate(error: Exception) -> None:
    with pytest.raises(type(error)):
        ingest(FakeJiraConnector(error=error))


# --------------------------------------------------------------- resilience


def test_an_unparseable_issue_is_reported_not_fatal() -> None:
    connector = FakeJiraConnector([make_raw("TRACK-1"), {}, make_raw("TRACK-3")])

    result = ingest(connector)

    assert result.retrieved_issues == 3
    assert len(result.issues) == 2
    assert result.generated_chunks == 2
    assert any("no key" in reason for _, reason in result.errors)


def test_an_empty_project_is_not_an_error() -> None:
    result = ingest(FakeJiraConnector([]))

    assert result.issues == []
    assert result.chunks == []
    assert result.errors == []
    assert result.epics == 0
    assert result.stories == 0


# ------------------------------------------------------------------ logging


def test_the_run_header_names_the_project_and_site(caplog) -> None:
    with caplog.at_level("INFO", logger="app.ingestion.jira_ingestion_service"):
        ingest(FakeJiraConnector([make_raw("TRACK-1")]))

    assert f"Ingesting Jira project {PROJECT} from {SITE}" in caplog.text


def test_the_summary_reports_the_funnel(caplog) -> None:
    connector = FakeJiraConnector(
        [
            make_raw("TRACK-10", issue_type="Epic"),
            make_raw("TRACK-25", parent="TRACK-10"),
        ]
    )

    with caplog.at_level("INFO", logger="app.ingestion.jira_ingestion_service"):
        ingest(connector)

    assert "Linked 1 issues to 1 epics" in caplog.text
    assert "2 Jira issues (1 epics, 1 stories, 1 linked to an epic)" in caplog.text


def test_the_credentials_are_never_logged(caplog) -> None:
    with caplog.at_level("DEBUG"):
        ingest(FakeJiraConnector([make_raw("TRACK-1")]))

    assert TOKEN.get_secret_value() not in caplog.text
    assert EMAIL not in caplog.text


def test_issue_descriptions_are_not_logged_at_info(caplog) -> None:
    sentinel = "sentinel-service-description"

    with caplog.at_level("INFO"):
        ingest(FakeJiraConnector([make_raw("TRACK-1", description=sentinel)]))

    assert sentinel not in caplog.text


# --------------------------------------------------------------- embedding


class FakeEmbedder:
    """Stands in for ChunkEmbedder, without a client or a credential."""

    batch_size = 30

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[int] = []

    def embed_chunks(self, chunks) -> EmbeddingRun:
        self.calls.append(len(chunks))
        if self.error is not None:
            raise self.error
        for position, chunk in enumerate(chunks):
            chunk.embedding = [float(position)] * 1536
            chunk.embedding_model = "fake-embedding-model"
        return EmbeddingRun(
            embedded=len(chunks),
            batches=(len(chunks) + 29) // 30,
            model="fake-embedding-model",
            dimensions=1536,
        )


def test_chunks_come_back_with_vectors() -> None:
    connector = FakeJiraConnector([make_raw("TRACK-1"), make_raw("TRACK-2")])
    embedder = FakeEmbedder()

    result = build_service(connector, embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, PROJECT
    )

    assert embedder.calls == [2]  # one call, holding every chunk
    assert result.embedded_chunks == result.generated_chunks == 2
    assert result.embedding_batches == 1
    assert result.embedding_model == "fake-embedding-model"
    assert result.embedding_dimensions == 1536
    assert all(chunk.embedding is not None for chunk in result.chunks)


def test_the_embedder_is_handed_the_chunk_objects_themselves() -> None:
    """Not a copy: the vectors have to land on what the caller already holds."""
    connector = FakeJiraConnector([make_raw("TRACK-1"), make_raw("TRACK-2")])

    result = build_service(connector, embedder=FakeEmbedder()).ingest(
        SITE, EMAIL, TOKEN, PROJECT
    )

    assert [chunk.embedding[0] for chunk in result.chunks] == [0.0, 1.0]


def test_without_an_embedder_the_run_still_produces_chunks() -> None:
    result = ingest(FakeJiraConnector([make_raw("TRACK-1")]))

    assert result.chunks
    assert all(chunk.embedding is None for chunk in result.chunks)
    assert result.embedded_chunks == 0
    assert result.embedding_model is None


def test_embedding_can_be_turned_off_for_one_run() -> None:
    connector = FakeJiraConnector([make_raw("TRACK-1")])
    embedder = FakeEmbedder()

    result = build_service(connector, embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, PROJECT, embed=False
    )

    assert embedder.calls == []
    assert result.chunks
    assert all(chunk.embedding is None for chunk in result.chunks)


def test_no_chunks_means_no_embedding_call() -> None:
    embedder = FakeEmbedder()

    build_service(FakeJiraConnector([]), embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, PROJECT
    )

    assert embedder.calls == []


def test_an_embedding_failure_fails_the_run() -> None:
    """Unlike a bad issue, a bad batch cannot be attributed or skipped."""
    connector = FakeJiraConnector([make_raw("TRACK-1")])

    with pytest.raises(EmbeddingError):
        build_service(connector, embedder=FakeEmbedder(EmbeddingError())).ingest(
            SITE, EMAIL, TOKEN, PROJECT
        )
