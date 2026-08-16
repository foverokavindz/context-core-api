"""Tests for SlackMessage -> SlackChunk.

Most assertions here compare the whole chunk text against a literal rather than
checking that a substring is present. Chunk text is the thing that will
eventually be embedded, so its exact content is the behaviour, not an
implementation detail - and this chunker's whole design decision is that the
content is the message and *nothing else*. A substring check would pass on a
chunk that had silently grown a "Channel: C0123456789" header.

The other half of the file guards the invariant the response funnel rests on:
one message in, one chunk out, however long or short the message is.
"""

import pytest

from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope
from app.ingestion.slack_chunker import SlackChunker
from app.models.slack_message import SlackMessage

CHANNEL = "C0123456789"
USER = "U0000000001"
TS = "1754810101.100100"


@pytest.fixture
def chunker() -> SlackChunker:
    return SlackChunker()


def make_message(
    message_ts: str = TS,
    *,
    text: str = "We should update the authentication flow.",
    author_id: str | None = USER,
    channel_id: str = CHANNEL,
) -> SlackMessage:
    """One normalised message, with every field a test might change."""
    return SlackMessage(
        channel_id=channel_id,
        message_ts=message_ts,
        author_id=author_id,
        text=text,
        external_id=f"{channel_id}:{message_ts}",
    )


# --------------------------------------------------------------- happy path


def test_a_message_renders_as_its_text_and_nothing_else(
    chunker: SlackChunker,
) -> None:
    chunk = chunker.chunk(make_message())

    assert chunk.content == "We should update the authentication flow."


def test_a_multi_line_message_keeps_its_shape(chunker: SlackChunker) -> None:
    chunk = chunker.chunk(
        make_message(text="Two things:\n\n- move validation\n- add a test")
    )

    assert chunk.content == "Two things:\n\n- move validation\n- add a test"


def test_the_text_is_carried_through_verbatim(chunker: SlackChunker) -> None:
    """The chunker rewrites nothing; the parser already normalised it."""
    text = "cc <@U0000000001> — see <https://example.com|the wiki> :tada: 100% \"done\""

    assert chunker.chunk(make_message(text=text)).content == text


# ------------------------------------------------------------------ metadata


def test_the_channel_id_is_carried_as_a_field(chunker: SlackChunker) -> None:
    assert chunker.chunk(make_message()).channel_id == CHANNEL


def test_the_timestamp_is_carried_as_a_field(chunker: SlackChunker) -> None:
    assert chunker.chunk(make_message()).message_ts == TS


def test_the_author_id_is_carried_as_a_field(chunker: SlackChunker) -> None:
    assert chunker.chunk(make_message()).author_id == USER


def test_a_message_with_no_author_chunks_fine(chunker: SlackChunker) -> None:
    chunk = chunker.chunk(make_message(author_id=None))

    assert chunk.author_id is None
    assert chunk.content == "We should update the authentication flow."


# -------------------------------------------------------- identifiers stay out


def test_identifiers_stay_out_of_the_text(chunker: SlackChunker) -> None:
    """Provenance for a retrieval layer to filter on, not prose to embed."""
    content = chunker.chunk(make_message()).content

    assert CHANNEL not in content
    assert USER not in content
    assert TS not in content


def test_a_short_message_is_not_outweighed_by_its_metadata(
    chunker: SlackChunker,
) -> None:
    """The reason this chunker renders no header at all."""
    chunk = chunker.chunk(make_message(text="Agreed."))

    assert chunk.content == "Agreed."


@pytest.mark.parametrize(
    "message",
    [
        make_message(author_id=None),
        make_message(text="Agreed."),
        make_message("999.000100"),
    ],
)
def test_an_absent_value_never_renders_as_none(
    chunker: SlackChunker, message: SlackMessage
) -> None:
    assert "None" not in chunker.chunk(message).content


def test_the_chunk_carries_no_thread_reaction_or_file_field(
    chunker: SlackChunker,
) -> None:
    """This version ignores them, so there is nowhere for them to reappear."""
    chunk = chunker.chunk(make_message())

    assert chunk.model_dump().keys() == {
        "channel_id",
        "message_ts",
        "author_id",
        "content",
        # Declared by the model, filled by the embedding service. The chunker
        # never touches them, which is what the next two lines check.
        "embedding",
        "embedding_model",
        # Declared by PermissionScope, filled by the ingestion service. The
        # chunker never touches these either.
        "team_id",
        "department_id",
        "access_scope",
        "external_data_source_id",
    }
    assert chunk.embedding is None
    assert chunk.embedding_model is None
    assert chunk.team_id is None
    assert chunk.department_id is None
    assert chunk.access_scope is ResourceAccessScope.TEAM


# --------------------------------------------------- one message, one chunk


def test_one_message_makes_one_chunk(chunker: SlackChunker) -> None:
    assert len(chunker.chunk_many([make_message()])) == 1


def test_every_message_makes_exactly_one_chunk(chunker: SlackChunker) -> None:
    messages = [make_message(f"10{n:02d}.000100") for n in range(25)]

    chunks = chunker.chunk_many(messages)

    assert len(chunks) == len(messages)


def test_messages_stay_separate(chunker: SlackChunker) -> None:
    """No grouping into conversations yet - that is the V1 rule."""
    chunks = chunker.chunk_many(
        [
            make_message("1000.000100", text="Should we move validation?"),
            make_message("1001.000100", text="Yes, into the service layer."),
        ]
    )

    assert len(chunks) == 2
    assert chunks[0].content == "Should we move validation?"
    assert chunks[1].content == "Yes, into the service layer."


def test_a_long_message_is_still_one_chunk(chunker: SlackChunker) -> None:
    """The V1 rule, tested against the case most likely to break it later."""
    paragraphs = "\n\n".join(
        f"Point {number}: some reasoning about it." for number in range(40)
    )

    chunks = chunker.chunk_many([make_message(text=paragraphs)])

    assert len(chunks) == 1
    assert "Point 0" in chunks[0].content
    assert "Point 39" in chunks[0].content


def test_chunks_keep_the_order_of_their_messages(chunker: SlackChunker) -> None:
    messages = [make_message(f"10{n:02d}.000100") for n in range(5)]

    chunks = chunker.chunk_many(messages)

    assert [chunk.message_ts for chunk in chunks] == [
        message.message_ts for message in messages
    ]


def test_no_messages_makes_no_chunks(chunker: SlackChunker) -> None:
    assert chunker.chunk_many([]) == []


def test_the_chunk_count_is_logged(chunker: SlackChunker, caplog) -> None:
    with caplog.at_level("INFO", logger="app.ingestion.slack_chunker"):
        chunker.chunk_many([make_message("1000.000100"), make_message("1001.000100")])

    assert "Generated 2 Slack chunks" in caplog.text


def test_message_text_is_never_logged(chunker: SlackChunker, caplog) -> None:
    with caplog.at_level("DEBUG"):
        chunker.chunk_many([make_message(text="sentinel-chunk-body")])

    assert "sentinel-chunk-body" not in caplog.text
