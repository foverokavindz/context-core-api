from pydantic import BaseModel, Field, SecretStr

REPOSITORY_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class GitHubIngestRequest(BaseModel):

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
    embed: bool = Field(
        default=True,
        description="Embed the chunks this run produces. Turn it off to fetch, "
        "parse and chunk without spending embedding quota - the response is the "
        "same shape either way, with null vectors.",
    )
    include_embeddings: bool = Field(
        default=False,
        description="Return each chunk's complete vector instead of the first "
        "few values. 1536 floats per chunk adds up fast - expect a response in "
        "the tens of megabytes on a real repository.",
    )
