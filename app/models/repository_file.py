"""RepositoryFile - one source file, normalised into our own shape.

This model is THE boundary of the pipeline. Everything upstream of it may know
about GitHub and PyGithub; everything downstream of it (the filter, the parsers,
the chunker) must not. When a Jira or Confluence connector is added later, it
produces this same model and the rest of the pipeline keeps working untouched.
"""

from pydantic import BaseModel

# Extension -> the language name we record on files and chunks.
#
# Single source of truth: the connector uses it to tag a RepositoryFile, and the
# parser registry uses the extension to decide which grammar to load. Adding a
# language later means adding a row here and registering a parser.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
}


def language_for_extension(extension: str | None) -> str | None:
    """Return the language name for a file extension, or None if unknown."""
    if extension is None:
        return None
    return LANGUAGE_BY_EXTENSION.get(extension.lower())


class RepositoryFile(BaseModel):
    """A single source file plus the repository coordinates it came from.

    `content` is already-decoded UTF-8 text. Files that could not be decoded
    never become a RepositoryFile - the connector reports them as errors and
    skips them, so anything that reaches the parser is known-good text.
    """

    # Where it came from.
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
