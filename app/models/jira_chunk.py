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

    embedding: list[float] | None = None
    embedding_model: str | None = None
