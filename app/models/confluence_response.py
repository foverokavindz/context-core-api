"""The debug/verification response returned by the Confluence ingest endpoint.

This response exists so a human can eyeball whether ingestion actually worked:
counts to check the funnel (retrieved -> parsed -> chunks), the normalised pages
themselves, a few sample chunks, and anything that was skipped.

Two different things can shorten what you see here, and they must not be
confused:

  truncated: true   the INGESTION stopped early. The space held more pages than
                    we fetched, because the page cap was reached. Every count
                    below describes only what was retrieved.

  full: false       the RESPONSE is a window onto a complete run. The page and
                    chunk lists are sampled and chunk text is shortened, but
                    every count is a true total and `truncated` is untouched.

Sampling never sets `truncated`.
"""

from pydantic import BaseModel

from app.models.confluence_page import ConfluencePage
from app.models.embedding_counts import EmbeddingCounts

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_PAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

# Sample chunks show the head of their text, not all of it. The ConfluenceChunk
# objects themselves always keep the complete content - this cap applies only
# to what gets serialised into the HTTP response. A wiki page runs much longer
# than a Jira description, so this preview cuts proportionally more.
CHUNK_CONTENT_PREVIEW_CHARS = 600

# Ceiling on how many pages one request will retrieve. Confluence pages at 100
# results per call, so this is five calls rather than an unbounded walk of a
# large space. When the cap is hit, `truncated` is set.
MAX_PAGES_PER_INGESTION = 500


class ConfluenceChunkSample(BaseModel):
    """One generated chunk, with its text possibly shortened for display."""

    page_id: str
    space_key: str
    title: str
    parent_id: str | None = None
    status: str | None = None
    content: str

    # The vector, or the head of it. `embedding` holds the complete 1536 floats
    # only when the request asked for them; otherwise `embedding_preview` shows
    # its first few values and `embedding` stays null. Both are null when the
    # chunk was never embedded, which `embedding_dimensions` also reports as
    # null - so "not embedded" and "embedded, shown briefly" never look alike.
    embedding: list[float] | None = None
    embedding_preview: list[float] | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None


class ConfluencePageError(BaseModel):
    """A page that was skipped, or a note about the run, and why.

    Keyed by page id where one page is at fault, and by space key for a note
    about the run as a whole. Neither is fatal - a single unparseable payload
    should not cost you the other 200 pages.
    """

    page: str
    reason: str


class ConfluenceIngestResponse(BaseModel):
    """What the Confluence ingest endpoint returns."""

    site_url: str

    # The space the key resolved to. Echoed back in full so a caller can see at
    # a glance that "TR" reached the space they meant.
    space_key: str
    space_id: str
    space_name: str | None = None

    # The funnel. retrieved -> parsed -> chunks produced. One page makes exactly
    # one chunk, so generated_chunks equals parsed_pages unless a page failed to
    # parse, and parsed_pages equals retrieved_pages for the same reason.
    retrieved_pages: int
    parsed_pages: int
    generated_chunks: int

    # True when the page cap stopped the run before the space ran out of pages,
    # meaning this is a partial view of the space. Not affected by sampling.
    truncated: bool = False

    # What embedding produced, in the shape all four endpoints report it.
    counts: EmbeddingCounts

    # The normalised pages, sampled unless `full` was requested. These are the
    # whole model rather than a summary: checking that bodies arrived as plain
    # text and that every page carries the space you asked for is the main
    # reason to look at this response at all.
    pages: list[ConfluencePage] = []

    sample_chunks: list[ConfluenceChunkSample] = []
    errors: list[ConfluencePageError] = []
