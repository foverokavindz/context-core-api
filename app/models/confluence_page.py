from app.models.permission_scope import PermissionScope

class ConfluencePage(PermissionScope):
    """A single Confluence page, flattened to text."""

    page_id: str
    space_id: str
    space_key: str
    space_name: str | None = None
    title: str
    parent_id: str | None = None
    status: str | None = None
    version_number: int | None = None
    content: str
