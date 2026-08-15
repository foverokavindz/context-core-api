"""Tests for the embedding step, with the OpenAI client replaced by a fake.

Nothing here touches the network or reads a credential. What is being tested is
not "does Azure return vectors" - it is the arithmetic and the ordering around
the call, which is where a batching bug would put the wrong vector on the wrong
chunk and stay invisible until retrieval started returning nonsense.
"""

from dataclasses import dataclass

import pytest

from app.core.exceptions import EmbeddingConfigurationError, EmbeddingError
from app.ingestion.embedding_service import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSIONS,
    MAX_EMBEDDING_INPUT_CHARS,
    ChunkEmbedder,
    embed_into,
)
from app.models.code_chunk import CodeChunk
from app.models.confluence_chunk import ConfluenceChunk
from app.models.jira_chunk import JiraChunk
from app.models.slack_chunk import SlackChunk

MODEL = "text-embedding-3-small"


def make_chunk(index: int, content: str | None = None) -> CodeChunk:
    return CodeChunk(
        repository="my-org/backend",
        branch="main",
        commit_sha="abc123",
        file_path=f"src/file{index}.ts",
        file_sha=f"sha{index}",
        language="typescript",
        symbol_type="function",
        symbol_name=f"fn{index}",
        start_line=1,
        end_line=5,
        content=content if content is not None else f"export function fn{index}() {{}}",
    )


@dataclass
class FakeItem:
    index: int
    embedding: list[float]


@dataclass
class FakeResponse:
    data: list[FakeItem]


class FakeEmbeddings:
    """Stands in for `client.embeddings`, recording every batch it is sent."""

    def __init__(self, behaviour=None) -> None:
        self.batches: list[list[str]] = []
        self.models: list[str] = []
        self.behaviour = behaviour

    def create(self, *, model: str, input: list[str]) -> FakeResponse:
        self.batches.append(list(input))
        self.models.append(model)
        if self.behaviour is not None:
            return self.behaviour(len(self.batches), list(input))
        return FakeResponse(
            data=[
                FakeItem(index=position, embedding=_vector_for(text))
                for position, text in enumerate(input)
            ]
        )


class FakeClient:
    def __init__(self, behaviour=None) -> None:
        self.embeddings = FakeEmbeddings(behaviour)


def _vector_for(text: str) -> list[float]:
    """A deterministic vector that identifies the text that produced it.

    The first value is a fingerprint of the input, which is what lets a test
    assert that a chunk got *its own* vector rather than merely some vector.
    """
    return [float(len(text)) + sum(map(ord, text[:8])) / 1000] + [0.0] * (
        EMBEDDING_DIMENSIONS - 1
    )


def build_embedder(behaviour=None, **kwargs) -> tuple[ChunkEmbedder, FakeClient]:
    embedder = ChunkEmbedder(deployment=MODEL, **kwargs)
    client = FakeClient(behaviour)
    # The client is built lazily on first use; pre-setting it is what keeps
    # these tests offline and free of environment variables.
    embedder._client = client
    return embedder, client


# ----------------------------------------------------------------- batching

def test_ninety_five_chunks_take_four_requests_of_at_most_thirty() -> None:
    embedder, client = build_embedder()
    chunks = [make_chunk(i) for i in range(95)]

    run = embedder.embed_chunks(chunks)

    assert [len(batch) for batch in client.embeddings.batches] == [30, 30, 30, 5]
    assert run.batches == 4


def test_no_request_ever_exceeds_the_batch_size() -> None:
    embedder, client = build_embedder()

    embedder.embed_chunks([make_chunk(i) for i in range(441)])

    assert client.embeddings.batches, "the fake was actually called"
    assert max(len(batch) for batch in client.embeddings.batches) <= EMBEDDING_BATCH_SIZE
    assert len(client.embeddings.batches) == 15  # ceil(441 / 30)


def test_an_exact_multiple_of_the_batch_size_makes_no_empty_request() -> None:
    embedder, client = build_embedder()

    embedder.embed_chunks([make_chunk(i) for i in range(60)])

    assert [len(batch) for batch in client.embeddings.batches] == [30, 30]


def test_a_single_chunk_takes_one_request() -> None:
    embedder, client = build_embedder()

    run = embedder.embed_chunks([make_chunk(0)])

    assert len(client.embeddings.batches) == 1
    assert run.batches == 1


def test_no_chunks_makes_no_request_at_all() -> None:
    embedder, client = build_embedder()

    run = embedder.embed_chunks([])

    assert client.embeddings.batches == []
    assert run.embedded == 0
    assert run.batches == 0


def test_the_batch_size_is_configurable_for_a_test_but_defaults_to_thirty() -> None:
    assert ChunkEmbedder().batch_size == EMBEDDING_BATCH_SIZE

    embedder, client = build_embedder(batch_size=4)
    embedder.embed_chunks([make_chunk(i) for i in range(9)])

    assert [len(batch) for batch in client.embeddings.batches] == [4, 4, 1]


def test_a_batch_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        ChunkEmbedder(batch_size=0)


# ------------------------------------------------------------------ ordering

def test_every_chunk_gets_the_vector_its_own_content_produced() -> None:
    embedder, _ = build_embedder()
    chunks = [make_chunk(i) for i in range(95)]

    embedder.embed_chunks(chunks)

    for chunk in chunks:
        assert chunk.embedding == _vector_for(chunk.content)


def test_the_chunk_order_is_unchanged() -> None:
    embedder, _ = build_embedder()
    chunks = [make_chunk(i) for i in range(35)]

    embedder.embed_chunks(chunks)

    assert [chunk.file_path for chunk in chunks] == [
        f"src/file{i}.ts" for i in range(35)
    ]


def test_vectors_are_ordered_by_index_not_by_arrival() -> None:
    """A response that comes back shuffled must still be put back correctly."""

    def shuffled(_call: int, batch: list[str]) -> FakeResponse:
        items = [
            FakeItem(index=position, embedding=_vector_for(text))
            for position, text in enumerate(batch)
        ]
        return FakeResponse(data=list(reversed(items)))

    embedder, _ = build_embedder(shuffled)
    chunks = [make_chunk(i) for i in range(5)]

    embedder.embed_chunks(chunks)

    for chunk in chunks:
        assert chunk.embedding == _vector_for(chunk.content)


def test_the_embedding_model_is_recorded_on_every_chunk() -> None:
    embedder, _ = build_embedder()
    chunks = [make_chunk(i) for i in range(3)]

    run = embedder.embed_chunks(chunks)

    assert run.model == MODEL
    assert all(chunk.embedding_model == MODEL for chunk in chunks)


def test_the_deployment_name_is_what_gets_called() -> None:
    embedder, client = build_embedder()

    embedder.embed_chunks([make_chunk(0)])

    assert client.embeddings.models == [MODEL]


# ---------------------------------------------------------------- validation

def test_a_short_batch_is_an_error_not_a_shifted_alignment() -> None:
    def one_short(_call: int, batch: list[str]) -> FakeResponse:
        return FakeResponse(
            data=[
                FakeItem(index=position, embedding=_vector_for(text))
                for position, text in enumerate(batch[:-1])
            ]
        )

    embedder, _ = build_embedder(one_short)
    chunks = [make_chunk(i) for i in range(5)]

    with pytest.raises(EmbeddingError):
        embedder.embed_chunks(chunks)


def test_an_over_long_batch_is_an_error() -> None:
    def one_extra(_call: int, batch: list[str]) -> FakeResponse:
        items = [
            FakeItem(index=position, embedding=_vector_for(text))
            for position, text in enumerate(batch)
        ]
        items.append(FakeItem(index=len(batch), embedding=_vector_for("extra")))
        return FakeResponse(data=items)

    embedder, _ = build_embedder(one_extra)

    with pytest.raises(EmbeddingError):
        embedder.embed_chunks([make_chunk(i) for i in range(3)])


def test_a_vector_of_the_wrong_width_is_refused() -> None:
    """A 3072-wide vector would not fit vector(1536); catch it here, not there."""

    def too_wide(_call: int, batch: list[str]) -> FakeResponse:
        return FakeResponse(
            data=[
                FakeItem(index=position, embedding=[0.0] * 3072)
                for position, _ in enumerate(batch)
            ]
        )

    embedder, _ = build_embedder(too_wide)

    with pytest.raises(EmbeddingError):
        embedder.embed_chunks([make_chunk(0)])


def test_the_dimension_matches_the_database_column() -> None:
    """The vector width and chunks.embedding must agree, or inserts will fail."""
    from app.entities.chunks.chunk import EMBEDDING_DIMENSIONS as COLUMN_WIDTH

    assert EMBEDDING_DIMENSIONS == COLUMN_WIDTH


# ------------------------------------------------------------ failure safety

def test_a_failure_partway_through_leaves_every_chunk_untouched() -> None:
    """Half a set of vectors is worse than none: it looks like it worked."""

    def fails_on_the_third(call: int, batch: list[str]) -> FakeResponse:
        if call == 3:
            return FakeResponse(data=[])
        return FakeResponse(
            data=[
                FakeItem(index=position, embedding=_vector_for(text))
                for position, text in enumerate(batch)
            ]
        )

    embedder, _ = build_embedder(fails_on_the_third)
    chunks = [make_chunk(i) for i in range(95)]

    with pytest.raises(EmbeddingError):
        embedder.embed_chunks(chunks)

    assert all(chunk.embedding is None for chunk in chunks)
    assert all(chunk.embedding_model is None for chunk in chunks)


def test_missing_configuration_is_reported_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(EmbeddingConfigurationError):
        ChunkEmbedder().embed_chunks([make_chunk(0)])


def test_the_api_key_never_reaches_the_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.invalid/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", MODEL)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    with pytest.raises(EmbeddingConfigurationError) as caught:
        ChunkEmbedder().embed_chunks([make_chunk(0)])

    assert "AZURE_OPENAI_API_KEY" in caught.value.message  # the name, not a value


# ------------------------------------------------------------ oversized input

def test_the_truncation_count_reaches_the_result() -> None:
    """A caller has to be able to see that some text was not embedded."""

    @dataclass
    class Result:
        chunks: list
        embedded_chunks: int = 0
        embedding_batches: int = 0
        embedding_model: str | None = None
        embedding_dimensions: int | None = None
        embedding_truncated_inputs: int = 0

    embedder, _ = build_embedder()
    result = Result(
        chunks=[
            make_chunk(0, "x" * (MAX_EMBEDDING_INPUT_CHARS + 1)),
            make_chunk(1, "short enough"),
            make_chunk(2, "y" * (MAX_EMBEDDING_INPUT_CHARS + 1)),
        ]
    )

    embed_into(result, embedder)

    assert result.embedded_chunks == 3
    assert result.embedding_truncated_inputs == 2


def test_an_oversized_chunk_is_truncated_for_the_request_only() -> None:
    embedder, client = build_embedder()
    huge = make_chunk(0, "x" * (MAX_EMBEDDING_INPUT_CHARS + 500))

    run = embedder.embed_chunks([huge])

    assert len(client.embeddings.batches[0][0]) == MAX_EMBEDDING_INPUT_CHARS
    assert run.truncated_inputs == 1
    # What is stored is still the exact slice the parser produced.
    assert len(huge.content) == MAX_EMBEDDING_INPUT_CHARS + 500
    assert huge.embedding is not None


def test_logging_tracks_batches_not_chunks(caplog: pytest.LogCaptureFixture) -> None:
    """95 chunks are 4 round trips, so 4 lines - not 95."""
    embedder, _ = build_embedder()

    with caplog.at_level("INFO", logger="app.ingestion.embedding_service"):
        embedder.embed_chunks([make_chunk(i) for i in range(95)])

    assert "Embedding 95 chunks in 4 request(s) of at most 30" in caplog.text
    for number in range(1, 5):
        assert f"[{number}/4] embedding" in caplog.text
    assert caplog.text.count("] embedding ") == 4


def test_chunk_content_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Source code is private; a log line is not the place for it."""
    embedder, _ = build_embedder()
    secret = "const CUSTOMER_SPECIFIC_BUSINESS_RULE = 42;"

    with caplog.at_level("DEBUG"):
        embedder.embed_chunks([make_chunk(0, secret)])

    assert secret not in caplog.text
    assert "CUSTOMER_SPECIFIC" not in caplog.text


def test_a_mismatched_batch_is_logged_before_it_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def one_short(_call: int, batch: list[str]) -> FakeResponse:
        return FakeResponse(
            data=[
                FakeItem(index=position, embedding=_vector_for(text))
                for position, text in enumerate(batch[:-1])
            ]
        )

    embedder, _ = build_embedder(one_short)

    with caplog.at_level("ERROR", logger="app.ingestion.embedding_service"):
        with pytest.raises(EmbeddingError):
            embedder.embed_chunks([make_chunk(i) for i in range(5)])

    assert "sent 5 chunks and got 4 vectors back" in caplog.text


def test_any_source_chunk_embeds_through_the_same_path() -> None:
    """The claim the EmbeddableChunk protocol makes, checked on all four."""
    embedder, client = build_embedder()
    chunks = [
        make_chunk(0, "export class AuthService {}"),
        JiraChunk(
            key="TRACK-25",
            project_key="TRACK",
            issue_type="Story",
            summary="Refresh tokens",
            content="Issue Key: TRACK-25\nRefresh tokens",
        ),
        ConfluenceChunk(
            page_id="7110680",
            space_id="6422530",
            space_key="TR",
            title="Authentication",
            content="Space: TrackIt (TR)\nAuthentication",
        ),
        SlackChunk(
            channel_id="C0123456789",
            message_ts="1754810101.100100",
            content="We should update the authentication flow.",
        ),
    ]

    run = embedder.embed_chunks(chunks)

    assert run.embedded == 4
    assert len(client.embeddings.batches) == 1  # all four in one request
    for chunk in chunks:
        assert chunk.embedding == _vector_for(chunk.content)
        assert chunk.embedding_model == MODEL


def test_ordinary_chunks_are_sent_verbatim() -> None:
    embedder, client = build_embedder()
    chunk = make_chunk(0, "export const answer = 42;")

    run = embedder.embed_chunks([chunk])

    assert client.embeddings.batches[0] == ["export const answer = 42;"]
    assert run.truncated_inputs == 0
