from pydantic import Field, computed_field

from app.models.common.permission_scope import PermissionScope


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

    external_id: str 
    chunk_index: int = 0 

    embedding: list[float] | None = None
    embedding_model: str | None = None

    @computed_field # type: ignore[prop-decorator]
    @property
    def chunk_type(self) -> str:
        """What `chunks.chunk_type` gets: this issue's issue_type, upper-cased.
        """
        return self.issue_type.upper()
