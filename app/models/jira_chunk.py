from pydantic import Field

from app.models.permission_scope import PermissionScope


class JiraChunk(PermissionScope):

    key: str
    project_key: str

    issue_type: str
    summary: str

    description: str | None = None
    status: str | None = None

    parent_key: str | None = None
    child_issues: list[str] = Field(default_factory=list)

    content: str

    external_id: str # = key, the same value the issue carries
    chunk_index: int = 0 # one issue makes one chunk, so this is always the first

    embedding: list[float] | None = None
    embedding_model: str | None = None
