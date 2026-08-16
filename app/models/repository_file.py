from app.models.permission_scope import PermissionScope

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

    # Which file it is.
    path: str
    file_name: str
    extension: str | None

    # What GitHub told us about it. Both optional: another source may not have
    # a content hash or may not report a size.
    file_sha: str | None = None
    size: int | None = None

    language: str | None = None

    content: str
