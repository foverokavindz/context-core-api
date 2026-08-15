"""Tests for the Confluence pipeline's orchestration.

Only the network boundary is faked. The real parser, the real storage flattener
and the real chunker all run, and the fake connector hands back raw REST-shaped
dicts rather than finished models - so these tests exercise the whole pipeline
below the HTTP layer and would notice any of those pieces being wired up wrong.

The fake also records how it was used, which is what backs the two wiring
guarantees worth stating out loud: the connector is called exactly once, and it
is closed before any parsing happens.
"""

import pytest
from pydantic import SecretStr

from app.connectors.confluence_connector import ConfluenceSnapshot
from app.core.exceptions import (
    ConfluenceAuthenticationError,
    ConfluenceNotFoundError,
    ConfluenceRateLimitError,
    EmbeddingError,
)
from app.ingestion.confluence_ingestion_service import ConfluenceIngestionService
from app.ingestion.embedding_service import EmbeddingRun

SITE = "https://example.atlassian.net"
EMAIL = "ingest@example.com"
TOKEN = SecretStr("confluence_fake_api_token_for_tests_only")
SPACE = "TR"
SPACE_ID = "6422530"
SPACE_NAME = "TrackIt"


# ------------------------------------------------------------------- fakes


def make_raw(
    page_id: str, *, title: str | None = None, parent: str | None = None,
    body: str | None = "<p>Body text.</p>",
) -> dict:
    """One raw page, shaped as the v2 API returns it."""
    raw: dict = {
        "id": page_id,
        "status": "current",
        "title": title if title is not None else f"Page {page_id}",
        "spaceId": SPACE_ID,
        "parentId": parent,
        "version": {"number": 1},
    }
    if body is not None:
        raw["body"] = {"storage": {"representation": "storage", "value": body}}
    return raw


class FakeConfluenceConnector:
    """Stands in for ConfluenceConnector, recording how it was used."""

    def __init__(
        self,
        raw_pages: list[dict] | None = None,
        *,
        truncated: bool = False,
        errors: list[tuple[str, str]] | None = None,
        error: Exception | None = None,
        space_name: str | None = SPACE_NAME,
    ) -> None:
        self.raw_pages = raw_pages if raw_pages is not None else []
        self.truncated = truncated
        self.errors = errors if errors is not None else []
        self.error = error
        self.space_name = space_name

        self.call_count = 0
        self.max_pages: int | None = None
        self.space_key: str | None = None
        self.closed = False

    def get_pages(
        self, space_key: str, *, max_pages: int | None = None
    ) -> ConfluenceSnapshot:
        self.call_count += 1
        self.max_pages = max_pages
        self.space_key = space_key

        if self.error is not None:
            raise self.error

        return ConfluenceSnapshot(
            site_url=SITE,
            space_id=SPACE_ID,
            space_key=space_key,
            space_name=self.space_name,
            retrieved_pages=len(self.raw_pages),
            truncated=self.truncated,
            pages=list(self.raw_pages),
            errors=list(self.errors),
        )

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeConfluenceConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def build_service(
    connector: FakeConfluenceConnector, **kwargs
) -> ConfluenceIngestionService:
    return ConfluenceIngestionService(
        connector_factory=lambda site_url, email, api_token: connector, **kwargs
    )


def ingest(connector: FakeConfluenceConnector, **kwargs):
    """Run one ingestion through the real parser and chunker.

    Keyword arguments go to `ingest`, not to the service, so a test that wants
    to configure the service itself builds one with `build_service`.
    """
    return build_service(connector).ingest(SITE, EMAIL, TOKEN, SPACE, **kwargs)


# --------------------------------------------------------------- happy path


def test_a_space_becomes_pages_and_chunks() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("1"), make_raw("2"), make_raw("3")]
    )

    result = ingest(connector)

    assert result.retrieved_pages == 3
    assert result.parsed_pages == 3
    assert result.generated_chunks == 3


def test_one_page_makes_exactly_one_chunk() -> None:
    """The invariant the whole response funnel rests on."""
    connector = FakeConfluenceConnector([make_raw(str(n)) for n in range(12)])

    result = ingest(connector)

    assert result.generated_chunks == result.parsed_pages


def test_the_pages_are_normalised_on_the_way_through() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("111", title="Authentication", body="<h2>JWT</h2><p>Yes.</p>")]
    )

    page = ingest(connector).pages[0]

    assert page.page_id == "111"
    assert page.title == "Authentication"
    assert page.content == "JWT\n\nYes."


def test_the_real_storage_flattener_runs() -> None:
    """Not a mocked one: the markup has to actually come out as prose."""
    connector = FakeConfluenceConnector(
        [make_raw("1", body="<ul><li>API</li><li>Database</li></ul>")]
    )

    assert ingest(connector).pages[0].content == "- API\n- Database"


def test_the_chunks_carry_the_page_text() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("1", title="Deployment", body="<p>Docker Compose.</p>")]
    )

    content = ingest(connector).chunks[0].content

    assert "Deployment" in content
    assert "Docker Compose." in content
    assert "<p>" not in content


def test_the_resolved_space_reaches_every_page_and_chunk() -> None:
    connector = FakeConfluenceConnector([make_raw("1"), make_raw("2")])

    result = ingest(connector)

    assert {page.space_key for page in result.pages} == {SPACE}
    assert {page.space_id for page in result.pages} == {SPACE_ID}
    assert {chunk.space_name for chunk in result.chunks} == {SPACE_NAME}


def test_the_space_comes_from_the_snapshot_not_the_request() -> None:
    """What was actually resolved, not what the caller typed."""
    connector = FakeConfluenceConnector([make_raw("1")], space_name=None)

    result = ingest(connector)

    assert result.space_name is None
    assert result.pages[0].space_name is None


def test_the_snapshot_metadata_is_carried_onto_the_result() -> None:
    result = ingest(FakeConfluenceConnector([make_raw("1")]))

    assert result.site_url == SITE
    assert result.space_id == SPACE_ID
    assert result.space_key == SPACE


def test_page_hierarchy_survives() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("101"), make_raw("102", parent="101")]
    )

    result = ingest(connector)

    assert result.pages[0].parent_id is None
    assert result.pages[1].parent_id == "101"


# ------------------------------------------------------------- empty space


def test_an_empty_space_is_not_an_error() -> None:
    result = ingest(FakeConfluenceConnector([]))

    assert result.retrieved_pages == 0
    assert result.parsed_pages == 0
    assert result.generated_chunks == 0
    assert result.errors == []


def test_an_empty_space_still_reports_which_space_it_was() -> None:
    result = ingest(FakeConfluenceConnector([]))

    assert result.space_key == SPACE
    assert result.space_id == SPACE_ID


# --------------------------------------------------------------- resilience


def test_one_malformed_page_does_not_cost_the_space() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("1"), {"title": "no id here"}, make_raw("3")]
    )

    result = ingest(connector)

    assert result.retrieved_pages == 3
    assert result.parsed_pages == 2
    assert result.generated_chunks == 2


def test_a_dropped_page_is_recorded() -> None:
    connector = FakeConfluenceConnector([make_raw("1"), {"title": "no id"}])

    result = ingest(connector)

    assert result.errors == [
        ("page #2", "Confluence returned a page with no id.")
    ]


def test_a_page_with_no_body_still_becomes_a_chunk() -> None:
    connector = FakeConfluenceConnector([make_raw("1", body=None)])

    result = ingest(connector)

    assert result.generated_chunks == 1
    assert result.pages[0].content == ""


def test_connector_notes_are_kept_alongside_parse_errors() -> None:
    connector = FakeConfluenceConnector(
        [make_raw("1"), {"no": "id"}],
        errors=[(SPACE, "Ingestion stopped at the 1-page cap; the space contains more.")],
    )

    result = ingest(connector)

    assert len(result.errors) == 2
    assert result.errors[0][0] == SPACE


# ------------------------------------------------------------------ wiring


def test_the_connector_is_called_exactly_once() -> None:
    """No request per page: one call fetches every page in the space."""
    connector = FakeConfluenceConnector([make_raw(str(n)) for n in range(20)])

    ingest(connector)

    assert connector.call_count == 1


def test_the_connector_is_closed_when_the_run_finishes() -> None:
    """The token must not outlive the fetch it was needed for."""
    connector = FakeConfluenceConnector([make_raw("1")])

    ingest(connector)

    assert connector.closed is True


def test_the_connector_is_closed_when_the_run_fails() -> None:
    connector = FakeConfluenceConnector(error=ConfluenceAuthenticationError())

    with pytest.raises(ConfluenceAuthenticationError):
        ingest(connector)

    assert connector.closed is True


def test_the_space_key_reaches_the_connector() -> None:
    connector = FakeConfluenceConnector([make_raw("1")])

    ingest(connector)

    assert connector.space_key == SPACE


def test_the_service_default_page_cap_is_applied() -> None:
    from app.models.confluence_response import MAX_PAGES_PER_INGESTION

    connector = FakeConfluenceConnector([make_raw("1")])

    ingest(connector)

    assert connector.max_pages == MAX_PAGES_PER_INGESTION


def test_a_request_page_cap_overrides_the_service_default() -> None:
    connector = FakeConfluenceConnector([make_raw("1")])

    ingest(connector, max_pages=7)

    assert connector.max_pages == 7


def test_a_configured_service_cap_is_used_when_the_request_gives_none() -> None:
    connector = FakeConfluenceConnector([make_raw("1")])

    build_service(connector, max_pages=42).ingest(SITE, EMAIL, TOKEN, SPACE)

    assert connector.max_pages == 42


# -------------------------------------------------------------- truncation


def test_truncation_is_carried_through_from_the_connector() -> None:
    connector = FakeConfluenceConnector([make_raw("1")], truncated=True)

    assert ingest(connector).truncated is True


def test_a_complete_run_is_not_truncated() -> None:
    assert ingest(FakeConfluenceConnector([make_raw("1")])).truncated is False


# ----------------------------------------------------------- error handling


@pytest.mark.parametrize(
    "error",
    [
        ConfluenceAuthenticationError(),
        ConfluenceNotFoundError(),
        ConfluenceRateLimitError(),
    ],
)
def test_a_whole_run_failure_propagates(error: Exception) -> None:
    """Bad credentials or a missing space are not per-page problems."""
    with pytest.raises(type(error)):
        ingest(FakeConfluenceConnector(error=error))


# ----------------------------------------------------------------- logging


def test_the_run_is_summarised(caplog) -> None:
    connector = FakeConfluenceConnector([make_raw("1"), make_raw("2")])

    with caplog.at_level(
        "INFO", logger="app.ingestion.confluence_ingestion_service"
    ):
        ingest(connector)

    assert f"Ingesting Confluence space {SPACE} from {SITE}" in caplog.text
    assert "into 2 chunks" in caplog.text


# ---------------------------------------------------------------- security


def test_the_api_token_never_reaches_the_logs(caplog) -> None:
    connector = FakeConfluenceConnector([make_raw("1")])

    with caplog.at_level("DEBUG"):
        ingest(connector)

    assert TOKEN.get_secret_value() not in caplog.text
    assert "confluence_fake" not in caplog.text
    assert EMAIL not in caplog.text


def test_a_page_body_never_reaches_the_logs(caplog) -> None:
    connector = FakeConfluenceConnector(
        [make_raw("1", body="<p>sentinel-service-page-body</p>")]
    )

    with caplog.at_level("DEBUG"):
        ingest(connector)

    assert "sentinel-service-page-body" not in caplog.text


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
    connector = FakeConfluenceConnector([make_raw("1"), make_raw("2")])
    embedder = FakeEmbedder()

    result = build_service(connector, embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, SPACE
    )

    assert embedder.calls == [2]  # one call, holding every chunk
    assert result.embedded_chunks == result.generated_chunks == 2
    assert result.embedding_batches == 1
    assert result.embedding_model == "fake-embedding-model"
    assert result.embedding_dimensions == 1536
    assert all(chunk.embedding is not None for chunk in result.chunks)


def test_the_embedder_is_handed_the_chunk_objects_themselves() -> None:
    """Not a copy: the vectors have to land on what the caller already holds."""
    connector = FakeConfluenceConnector([make_raw("1"), make_raw("2")])

    result = build_service(connector, embedder=FakeEmbedder()).ingest(
        SITE, EMAIL, TOKEN, SPACE
    )

    assert [chunk.embedding[0] for chunk in result.chunks] == [0.0, 1.0]


def test_without_an_embedder_the_run_still_produces_chunks() -> None:
    result = ingest(FakeConfluenceConnector([make_raw("1")]))

    assert result.chunks
    assert all(chunk.embedding is None for chunk in result.chunks)
    assert result.embedded_chunks == 0
    assert result.embedding_model is None


def test_embedding_can_be_turned_off_for_one_run() -> None:
    connector = FakeConfluenceConnector([make_raw("1")])
    embedder = FakeEmbedder()

    result = build_service(connector, embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, SPACE, embed=False
    )

    assert embedder.calls == []
    assert result.chunks
    assert all(chunk.embedding is None for chunk in result.chunks)


def test_no_chunks_means_no_embedding_call() -> None:
    embedder = FakeEmbedder()

    build_service(FakeConfluenceConnector([]), embedder=embedder).ingest(
        SITE, EMAIL, TOKEN, SPACE
    )

    assert embedder.calls == []


def test_an_embedding_failure_fails_the_run() -> None:
    """Unlike a bad page, a bad batch cannot be attributed or skipped."""
    connector = FakeConfluenceConnector([make_raw("1")])

    with pytest.raises(EmbeddingError):
        build_service(connector, embedder=FakeEmbedder(EmbeddingError())).ingest(
            SITE, EMAIL, TOKEN, SPACE
        )
