"""JiraChunk - one Epic or Story rendered as embeddable text.

This is the output of the Jira pipeline and its handover point, exactly as
CodeChunk is for GitHub. The chunker produces one of these per issue; the
embedding service then fills in `embedding` and `embedding_model` on the very
same objects, in place. It still carries no database id and no storage-specific
field - which row it became is the persistence layer's business.

For this first version the mapping is one chunk per issue - no splitting by
tokens, characters, paragraphs or headings. Jira descriptions are short enough
that the simplest possible baseline is the right place to start.
"""

from pydantic import BaseModel, Field


class JiraChunk(BaseModel):
    """The rendered text of one issue, plus the issue fields as metadata.

    The issue fields are repeated here rather than referenced so a chunk is
    self-describing once it leaves the pipeline - the same reason CodeChunk
    copies its repository coordinates. They will likely move into a shared
    metadata object once a second non-code source exists.
    """

    key: str
    project_key: str

    issue_type: str
    summary: str

    description: str | None = None
    status: str | None = None

    parent_key: str | None = None
    child_issues: list[str] = Field(default_factory=list)

    # What we expect to embed: clean human-readable text, assembled by the
    # chunker. For an Epic this names its children by key only - their
    # descriptions live in their own chunks.
    content: str

    # Filled by the embedding service after chunking, on this same object, and
    # only once every batch of a run has come back intact - so these are either
    # both set or both None, never one without the other.
    embedding: list[float] | None = None
    embedding_model: str | None = None
