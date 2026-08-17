from pydantic import computed_field

from app.models.common.permission_scope import PermissionScope


SYMBOL_TYPES: tuple[str, ...] = (
    "class",
    "method",
    "function",
    "interface",
    "enum",
    "type_alias",
    "file",  # whole-file fallback, see the parser's fallback behaviour
)

class CodeChunk(PermissionScope):

    repository: str
    branch: str
    commit_sha: str

    file_path: str
    file_name: str | None = None
    extension: str | None = None
    file_sha: str | None = None

    external_id: str # = file_path, the same value the resource file carries
    chunk_index: int = 0 # a file yields several chunks, so the parser numbers them; the default is only correct for a file that produced exactly one

    language: str

    symbol_type: str
    symbol_name: str | None = None
    parent_symbol: str | None = None

    start_line: int
    end_line: int

    content: str

    embedding: list[float] | None = None
    embedding_model: str | None = None

    @computed_field # type: ignore[prop-decorator]
    @property
    def chunk_type(self) -> str:
        """What `chunks.chunk_type` gets: this chunk's symbol_type, upper-cased.

        Derived rather than stored so the two cannot disagree - the parser sets
        symbol_type in one place and this follows it, including for the values
        the old enum could not hold (INTERFACE, ENUM, TYPE_ALIAS).
        """
        return self.symbol_type.upper()
