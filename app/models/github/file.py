from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.common.permission_scope import PermissionScope

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
}


def language_for_extension(extension: str | None) -> str | None:
    """Return the language name for a file extension, or None if unknown."""
    if extension is None:
        return None
    return LANGUAGE_BY_EXTENSION.get(extension.lower())


class RepositoryFile(PermissionScope):

    repository: str
    branch: str
    commit_sha: str

    file_path: str
    file_name: str
    extension: str | None

    file_sha: str | None = None
    size: int | None = None

    language: str | None = None

    content: str

    external_id: str 
    title: str | None = None 
    version_key: str | None = None 
    resource_type: ResourceType = ResourceType.GITHUB_FILE
