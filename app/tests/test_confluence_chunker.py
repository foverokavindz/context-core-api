"""Tests for ConfluencePage -> ConfluenceChunk.

Most assertions here compare the whole rendered chunk against a literal rather
than checking that a substring is present. Chunk text is the thing that will
eventually be embedded, so its exact layout - which line comes first, where the
blank line falls, what an absent value renders as - is the behaviour, not an
implementation detail. A substring check would pass on a chunk that had silently
grown a "Version: None" line.

The other half of the file guards the invariant the whole response funnel rests
on: one page in, one chunk out, however long the page is.
"""

import pytest

from app.ingestion.confluence_chunker import NO_CONTENT_TEXT, ConfluenceChunker
from app.models.confluence_page import ConfluencePage

SPACE_KEY = "TR"
SPACE_ID = "6422530"
SPACE_NAME = "TrackIt"


@pytest.fixture
def chunker() -> ConfluenceChunker:
    return ConfluenceChunker()


def make_page(
    page_id: str = "111",
    *,
    title: str = "Authentication",
    content: str = "TrackIt uses JWT authentication.",
    space_name: str | None = SPACE_NAME,
    parent_id: str | None = None,
    status: str | None = "current",
    version_number: int | None = 7,
) -> ConfluencePage:
    """One normalised page, with every field a test might take away."""
    return ConfluencePage(
        page_id=page_id,
        space_id=SPACE_ID,
        space_key=SPACE_KEY,
        space_name=space_name,
        title=title,
        parent_id=parent_id,
        status=status,
        version_number=version_number,
        content=content,
        external_id=page_id,
        version_key=None if version_number is None else str(version_number),
    )


# --------------------------------------------------------------- happy path


def test_a_page_renders_as_space_title_and_text(chunker: ConfluenceChunker) -> None:
    chunk = chunker.chunk(make_page())

    assert chunk.content == (
        "Space: TrackIt (TR)\n"
        "Page: Authentication\n"
        "\n"
        "Content:\n"
        "TrackIt uses JWT authentication."
    )


def test_a_multi_paragraph_page_keeps_its_spacing(
    chunker: ConfluenceChunker,
) -> None:
    page = make_page(
        content=(
            "TrackIt uses JWT authentication.\n"
            "\n"
            "Users log in using their email and password.\n"
            "After successful authentication, the system issues a JWT."
        )
    )

    assert chunker.chunk(page).content == (
        "Space: TrackIt (TR)\n"
        "Page: Authentication\n"
        "\n"
        "Content:\n"
        "TrackIt uses JWT authentication.\n"
        "\n"
        "Users log in using their email and password.\n"
        "After successful authentication, the system issues a JWT."
    )


def test_the_space_falls_back_to_its_key_when_unnamed(
    chunker: ConfluenceChunker,
) -> None:
    chunk = chunker.chunk(make_page(space_name=None))

    assert chunk.content.startswith("Space: TR\n")


def test_an_empty_page_does_not_leave_a_dangling_header(
    chunker: ConfluenceChunker,
) -> None:
    """Empty pages are normal in Confluence: placeholders and tree parents."""
    chunk = chunker.chunk(make_page(content=""))

    assert chunk.content == (
        "Space: TrackIt (TR)\n"
        "Page: Authentication\n"
        "\n"
        "Content:\n"
        f"{NO_CONTENT_TEXT}"
    )


# ---------------------------------------------------------------- what is in


def test_the_title_is_in_the_text(chunker: ConfluenceChunker) -> None:
    chunk = chunker.chunk(make_page(title="System Architecture"))

    assert "System Architecture" in chunk.content


def test_the_space_is_in_the_text(chunker: ConfluenceChunker) -> None:
    """Which space a page came from is context the body cannot supply."""
    assert SPACE_KEY in chunker.chunk(make_page()).content


def test_the_page_text_is_in_the_text(chunker: ConfluenceChunker) -> None:
    chunk = chunker.chunk(make_page(content="Deployed with Docker Compose."))

    assert "Deployed with Docker Compose." in chunk.content


# --------------------------------------------------------------- what is out


def test_no_storage_markup_reaches_the_chunk(chunker: ConfluenceChunker) -> None:
    """The page is already flat by this point; this is the belt-and-braces."""
    chunk = chunker.chunk(make_page(content="Auth\n\n- One\n- Two"))

    for fragment in ("<p>", "<h1>", "<ul>", "ac:structured-macro", "<"):
        assert fragment not in chunk.content


def test_identifiers_stay_out_of_the_text(chunker: ConfluenceChunker) -> None:
    """Provenance for a retrieval layer to filter on, not prose to embed."""
    page = make_page("6422643", parent_id="6422530", version_number=12)
    content = chunker.chunk(page).content

    assert "6422643" not in content
    assert "6422530" not in content
    assert "12" not in content


@pytest.mark.parametrize(
    "page",
    [
        make_page(space_name=None),
        make_page(status=None),
        make_page(version_number=None),
        make_page(parent_id=None),
        make_page(content=""),
        make_page(title=""),
    ],
)
def test_an_absent_value_never_renders_as_none(
    chunker: ConfluenceChunker, page: ConfluencePage
) -> None:
    assert "None" not in chunker.chunk(page).content


# ----------------------------------------------------------------- metadata


def test_the_page_fields_are_carried_onto_the_chunk(
    chunker: ConfluenceChunker,
) -> None:
    page = make_page("111", parent_id="100", status="current", version_number=7)

    chunk = chunker.chunk(page)

    assert chunk.page_id == "111"
    assert chunk.space_id == SPACE_ID
    assert chunk.space_key == SPACE_KEY
    assert chunk.space_name == SPACE_NAME
    assert chunk.title == "Authentication"
    assert chunk.parent_id == "100"
    assert chunk.status == "current"
    assert chunk.version_number == 7


def test_the_version_number_survives_onto_the_chunk(
    chunker: ConfluenceChunker,
) -> None:
    """Kept as provenance for a future sync, even though nothing reads it yet."""
    assert chunker.chunk(make_page(version_number=42)).version_number == 42


def test_the_parent_id_survives_onto_the_chunk(chunker: ConfluenceChunker) -> None:
    assert chunker.chunk(make_page(parent_id="101")).parent_id == "101"


# ---------------------------------------------------- one page, one chunk


def test_one_page_makes_exactly_one_chunk(chunker: ConfluenceChunker) -> None:
    assert len(chunker.chunk_many([make_page()])) == 1


def test_every_page_makes_a_chunk(chunker: ConfluenceChunker) -> None:
    pages = [make_page(str(number)) for number in range(1, 26)]

    assert len(chunker.chunk_many(pages)) == len(pages)


def test_nothing_to_chunk_is_not_an_error(chunker: ConfluenceChunker) -> None:
    assert chunker.chunk_many([]) == []


def test_the_order_of_the_pages_is_kept(chunker: ConfluenceChunker) -> None:
    pages = [make_page("1"), make_page("2"), make_page("3")]

    chunks = chunker.chunk_many(pages)

    assert [chunk.page_id for chunk in chunks] == ["1", "2", "3"]


def test_a_long_page_with_many_headings_is_still_one_chunk(
    chunker: ConfluenceChunker,
) -> None:
    """The V1 rule, tested against the case most likely to break it later."""
    sections = "\n\n".join(
        f"Section {number}\n\nBody text for section {number}." for number in range(30)
    )
    page = make_page(content=sections)

    chunks = chunker.chunk_many([page])

    assert len(chunks) == 1
    assert "Section 0" in chunks[0].content
    assert "Section 29" in chunks[0].content


def test_the_whole_page_reaches_the_one_chunk(chunker: ConfluenceChunker) -> None:
    body = "x" * 50_000
    page = make_page(content=body)

    assert body in chunker.chunk(page).content


def test_the_chunk_count_is_logged(chunker: ConfluenceChunker, caplog) -> None:
    with caplog.at_level("INFO", logger="app.ingestion.confluence_chunker"):
        chunker.chunk_many([make_page("1"), make_page("2")])

    assert "Generated 2 Confluence chunks" in caplog.text
