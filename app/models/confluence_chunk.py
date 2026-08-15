"""ConfluenceChunk - one wiki page rendered as embeddable text.

This is the output of the Confluence pipeline and its handover point, exactly as
JiraChunk is for Jira and CodeChunk is for GitHub. The chunker produces one of
these per page; the embedding service then fills in `embedding` and
`embedding_model` on the very same objects, in place. It still carries no
database id and no storage-specific field.

For this first version the mapping is one chunk per page - no splitting by
headings, tokens, characters, paragraphs or sections. That gives a baseline to
measure a heading-based strategy against later; measuring it is not part of this
version.
"""

from pydantic import BaseModel


class ConfluenceChunk(BaseModel):
    """The rendered text of one page, plus the page fields as metadata.

    The page fields are repeated here rather than referenced so a chunk is
    self-describing once it leaves the pipeline - the same reason JiraChunk
    copies its issue fields. They will likely move into a shared metadata object
    once GitHub, Jira and Confluence can be compared side by side.
    """

    page_id: str

    space_id: str
    space_key: str
    space_name: str | None = None

    title: str

    parent_id: str | None = None
    status: str | None = None
    version_number: int | None = None

    # What we expect to embed: the space, the title and the page's readable
    # text. The ids and the version number above stay out of it deliberately -
    # they are provenance for a retrieval layer to filter on, not meaning for a
    # model to embed.
    content: str

    # Filled by the embedding service after chunking, on this same object, and
    # only once every batch of a run has come back intact - so these are either
    # both set or both None, never one without the other. A page longer than
    # MAX_EMBEDDING_INPUT_CHARS is embedded on its opening section; `content`
    # itself is never shortened.
    embedding: list[float] | None = None
    embedding_model: str | None = None
