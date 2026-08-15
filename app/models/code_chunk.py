"""CodeChunk - one logical unit of source code extracted from a file.

This is the output of the whole pipeline and the handover point to whatever gets
built next. The parser produces one of these per symbol; the embedding service
then fills in `embedding` and `embedding_model` on the very same objects, in
place, so nothing upstream has to hand back a second list.

It still carries no database id and no storage-specific field. A chunk knows
what it is and what it means; which row it became is the persistence layer's
business, and that layer does not exist yet.
"""

from pydantic import BaseModel

# The symbol kinds the TypeScript parser can currently produce. Kept as a plain
# tuple of strings rather than an Enum: chunks are handed to storage and to JSON
# consumers, and a new language adding its own kinds should not require editing
# a shared enum.
SYMBOL_TYPES: tuple[str, ...] = (
    "class",
    "method",
    "function",
    "interface",
    "enum",
    "type_alias",
    "file",  # whole-file fallback, see the parser's fallback behaviour
)


class CodeChunk(BaseModel):
    """A span of original source with the context needed to make sense of it.

    `content` is always an exact slice of the file it came from - never
    reconstructed from the syntax tree - so it can be shown to a user or fed to
    a model verbatim.
    """

    # Repository coordinates, copied from the RepositoryFile so a chunk is
    # self-describing once it leaves the pipeline.
    repository: str
    branch: str
    commit_sha: str

    file_path: str
    file_sha: str | None = None

    language: str

    # What kind of symbol this is, what it is called, and what encloses it.
    # `symbol_name` is None for the whole-file fallback chunk; `parent_symbol`
    # is None for anything declared at the top level of a file.
    symbol_type: str
    symbol_name: str | None = None
    parent_symbol: str | None = None

    # 1-based and inclusive, matching what an editor shows.
    start_line: int
    end_line: int

    content: str

    # Filled by the embedding service after chunking, on this same object, and
    # only once every batch of a run has come back intact - so these are either
    # both set or both None, never one without the other. `embedding_model` is
    # the deployment that produced the vector: without it, a later model change
    # would leave no way to tell which vectors are stale.
    embedding: list[float] | None = None
    embedding_model: str | None = None
