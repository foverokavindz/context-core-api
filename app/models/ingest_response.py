from pydantic import BaseModel

from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.code_chunk import CodeChunk
from app.models.embedding_counts import EmbeddingCounts
from app.models.permission_scope import PermissionScope

SAMPLE_FILES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_FILES_PER_INGESTION = 500


class FileSummary(PermissionScope):
    """One file, shaped like the `resources` row it is destined to become."""

    repository: str
    branch: str
    commit_sha: str

    file_path: str # `path` on RepositoryFile, which is the connector's model rather than a resource file. Named for what CodeChunk already calls it, so the two halves of a run agree
    file_name: str
    extension: str | None = None

    file_sha: str | None = None # the blob SHA: what tells a re-ingestion that this file changed
    language: str | None = None
    size: int | None = None

    external_id: str # = file_path, and the key chunks use to find this resource
    title: str | None = None # = file_name
    version_key: str | None = None # = file_sha
    resource_type: ResourceType = ResourceType.GITHUB_FILE


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
