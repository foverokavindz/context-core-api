"""The embedding tally every ingest response carries.

The four sources disagree about almost everything - a run retrieves files, or
issues, or pages, or messages, and each response reports its own funnel in its
own words. They agree completely about this: how many chunks were produced, how
many vectors came back, and what it cost to get them.

So this is the one response model shared by all four endpoints. `chunks` and
`embeddings` being equal is the assertion worth reading first: it says every
chunk came back from the embedding service with a vector of its own. On a run
where embedding was skipped, `embeddings` and `embedding_batches` are 0 and the
model and width are null - which reads as "not embedded" rather than as a
failure.

Source-specific counts deliberately stay out of here. `accepted_files`,
`retrieved_issues`, `parsed_pages` and the rest live at the top level of their
own responses, where they always have.
"""

from pydantic import BaseModel


class EmbeddingCounts(BaseModel):
    """What one run's embedding pass produced."""

    chunks: int
    embeddings: int

    # ceil(chunks / EMBEDDING_BATCH_SIZE) on a successful run - 15 requests for
    # 441 chunks. This is the number against a rate limit, not against a bill.
    embedding_batches: int

    embedding_model: str | None = None
    embedding_dimensions: int | None = None

    # Chunks whose text was too long for one request and were embedded on their
    # opening MAX_EMBEDDING_INPUT_CHARS. Reported rather than left in the log
    # because it is real, quiet data loss: the stored content is complete, but
    # the vector only represents the part that was sent, so those chunks are
    # findable by their beginning and not by their end. Confluence is where this
    # bites - a long wiki page runs past the cap where an issue or a message
    # never will.
    truncated_inputs: int = 0
