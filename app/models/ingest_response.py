from pydantic import BaseModel

from app.models.embedding_counts import EmbeddingCounts
from app.models.permission_scope import PermissionScope

SAMPLE_FILES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

CHUNK_CONTENT_PREVIEW_CHARS = 600

EMBEDDING_PREVIEW_VALUES = 8

MAX_FILES_PER_INGESTION = 500


class FileSummary(PermissionScope):

    path: str
    language: str | None = None
    size: int | None = None


class ChunkSample(PermissionScope):
    """One extracted chunk, with its source possibly shortened for display."""

    file_path: str
    symbol_type: str
    symbol_name: str | None = None
    parent_symbol: str | None = None
    start_line: int
    end_line: int
    content: str

    embedding: list[float] | None = None
    embedding_preview: list[float] | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None


class FileError(BaseModel):
    
    file: str
    reason: str


class IngestResponse(BaseModel):
    """What the ingest endpoint returns."""

    repository: str
    branch: str
    commit_sha: str

    discovered_files: int
    accepted_files: int
    parsed_files: int
    generated_chunks: int

    truncated: bool = False

    counts: EmbeddingCounts

    resource_files: list[FileSummary] = []

    chunks: list[ChunkSample] = []
    errors: list[FileError] = []
