from app.entities.knowledge_sources.resource_type import ResourceType
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

    external_id: str # = page_id
    version_key: str | None = None # = str(version_number). Confluence increments it on every edit, which is what version_key is for
    resource_type: ResourceType = ResourceType.CONFLUENCE_PAGE
