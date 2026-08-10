"""The request body for POST /api/v1/github/ingest."""

from pydantic import BaseModel, Field, SecretStr

# owner/name, the form GitHub's API expects. Anchored so a full URL or a path
# with extra segments is rejected up front rather than becoming a confusing 404.
REPOSITORY_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class GitHubIngestRequest(BaseModel):
    """A request to ingest one repository.

    The token is a SecretStr rather than a plain str on purpose. Pydantic prints
    it as `**********` in every repr, log line and `model_dump()`, so it cannot
    leak through an accidental log statement or by being echoed into a response.
    `.get_secret_value()` is called in exactly one place - where the GitHub
    client is constructed - and the value is never stored anywhere.
    """

    token: SecretStr = Field(
        description="A GitHub access token with read access to the repository. "
        "Held in memory for this request only; never logged or stored.",
    )
    repository: str = Field(
        pattern=REPOSITORY_PATTERN,
        description='Repository in "owner/name" form, e.g. "my-org/backend".',
        examples=["my-org/backend"],
    )
    branch: str | None = Field(
        default=None,
        description="Branch to read. Defaults to the repository's default branch.",
        examples=["main"],
    )
    full: bool = Field(
        default=False,
        description="Return every file and every chunk instead of a sample, with "
        "chunk contents left untruncated. The pipeline always processes the whole "
        "repository either way - this only controls how much of the result is "
        "serialised. Expect a large response on a real repository.",
    )
    max_files: int | None = Field(
        default=None,
        ge=1,
        description="Override how many accepted files this run will download. "
        "Defaults to MAX_FILES_PER_INGESTION. Each file costs one GitHub API "
        "call, so raising this makes the request slower.",
        examples=[500],
    )
