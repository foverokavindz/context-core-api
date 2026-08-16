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

    # What GitHub told us about it. Both optional: another source may not have
    # a content hash or may not report a size.
    file_sha: str | None = None
    size: int | None = None

    language: str | None = None

    content: str

    external_id: str # = file_path. A path means something only inside its repository, which is why resources scopes uniqueness to (source, external_id)
    title: str | None = None # = file_name. The path is what identifies the file; the bare name is what a person reads
    version_key: str | None = None # = file_sha, the blob SHA GitHub changes whenever the file's bytes do
    resource_type: ResourceType = ResourceType.GITHUB_FILE
