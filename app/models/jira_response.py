from pydantic import BaseModel

from app.models.embedding_counts import EmbeddingCounts
from app.models.jira_chunk import JiraChunk
from app.models.jira_issue import JiraIssue

SAMPLE_ISSUES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_ISSUES_PER_INGESTION = 500


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

    chunks: list[JiraChunk] = []
    errors: list[JiraIssueError] = []
