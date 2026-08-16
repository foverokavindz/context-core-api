from pydantic import BaseModel

from app.models.code_chunk import CodeChunk
from app.models.embedding_counts import EmbeddingCounts
from app.models.permission_scope import PermissionScope

SAMPLE_FILES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_FILES_PER_INGESTION = 500


class FileSummary(PermissionScope):

    repository: str
    branch: str
    commit_sha: str

    path: str
    file_name: str
    extension: str | None = None

    file_sha: str | None = None # the blob SHA: what tells a re-ingestion that this file changed
    language: str | None = None
    size: int | None = None


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

    chunks: list[CodeChunk] = []
    errors: list[FileError] = []
