from app.models.permission_scope import PermissionScope

class ConfluenceChunk(PermissionScope):
    page_id: str

    space_id: str
    space_key: str
    space_name: str | None = None

    title: str

    parent_id: str | None = None
    status: str | None = None
    version_number: int | None = None

    content: str

    embedding: list[float] | None = None
    embedding_model: str | None = None
