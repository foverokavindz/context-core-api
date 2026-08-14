"""The debug/verification response returned by the ingest endpoint.

This response exists so a human can eyeball whether ingestion actually worked:
counts to check the funnel (discovered -> accepted -> parsed -> chunks), a few
sample files, a few sample chunks, and anything that was skipped.

It is deliberately a *projection*. The full IngestionResult the service builds
holds every RepositoryFile and every CodeChunk; only a slice of that is
serialised, because a real repository would otherwise return megabytes of source
over HTTP.
"""

from pydantic import BaseModel

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_FILES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

# Sample chunks show the head of their source, not all of it. The CodeChunk
# objects themselves always keep the complete span - this cap applies only to
# what gets serialised into the HTTP response.
CHUNK_CONTENT_PREVIEW_CHARS = 600

# Ceiling on how many accepted files one request will download. Fetching file
# contents costs one GitHub API call each, so an unbounded run against a large
# repository would take minutes and risk an HTTP timeout. When the cap is hit,
# `truncated` is set and `discovered_files` still reports the true total.
MAX_FILES_PER_INGESTION = 500


class FileSummary(BaseModel):
    """One accepted file, without its contents."""

    path: str
    language: str | None = None
    size: int | None = None


class ChunkSample(BaseModel):
    """One extracted chunk, with its source possibly shortened for display."""

    file_path: str
    symbol_type: str
    symbol_name: str | None = None
    parent_symbol: str | None = None
    start_line: int
    end_line: int
    content: str


class FileError(BaseModel):
    """A file that was skipped, and why.

    A skipped file never fails the run - a binary blob, an undecodable file or
    one unreadable file should not cost you the other 400.
    """

    file: str
    reason: str


class IngestResponse(BaseModel):
    """What the ingest endpoint returns."""

    repository: str
    branch: str
    commit_sha: str

    # The funnel. discovered -> accepted (survived the filter) -> parsed
    # (successfully read and parsed) -> chunks produced.
    discovered_files: int
    accepted_files: int
    parsed_files: int
    generated_chunks: int

    # True when the file cap or GitHub's own tree truncation limited the run,
    # meaning this is a partial view of the repository.
    truncated: bool = False

    files: list[FileSummary] = []
    chunks: list[ChunkSample] = []
    errors: list[FileError] = []
