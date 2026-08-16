from pydantic import BaseModel

from app.models.embedding_counts import EmbeddingCounts
from app.models.jira_issue import JiraIssue
from app.models.permission_scope import PermissionScope

SAMPLE_ISSUES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

CHUNK_CONTENT_PREVIEW_CHARS = 600

MAX_ISSUES_PER_INGESTION = 500


class JiraChunkSample(PermissionScope):
    """One generated chunk, with its text possibly shortened for display."""

    key: str
    issue_type: str
    summary: str
    status: str | None = None
    parent_key: str | None = None
    content: str

    embedding: list[float] | None = None
    embedding_preview: list[float] | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None


class JiraIssueError(BaseModel):

    issue: str
    reason: str


class JiraIngestResponse(BaseModel):
    """What the Jira ingest endpoint returns."""

    site_url: str
    project_key: str

    retrieved_issues: int
    epics: int
    stories: int
    generated_chunks: int

    truncated: bool = False

    counts: EmbeddingCounts

    resource_files: list[JiraIssue] = []

    sample_chunks: list[JiraChunkSample] = []
    errors: list[JiraIssueError] = []
