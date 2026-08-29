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

    external_id: str
    chunk_index: int = 0 

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
        """
        return self.symbol_type.upper()
