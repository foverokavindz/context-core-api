from pydantic import BaseModel

from app.models.code_chunk import CodeChunk
from app.models.embedding_counts import EmbeddingCounts
from app.models.repository_file import RepositoryFile

SAMPLE_FILES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_FILES_PER_INGESTION = 500


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

    resource_files: list[RepositoryFile] = [] # the connector's own model, returned as it stands. One definition of a GitHub file per run, so a field added to it cannot go missing here

    chunks: list[CodeChunk] = []
    errors: list[FileError] = []
