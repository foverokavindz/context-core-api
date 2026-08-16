"""The request body for POST /api/v1/jira/ingest."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# A Jira Cloud site root, and nothing more. Anchored so a full issue URL, a path
# with extra segments or a bare hostname is rejected up front rather than
# becoming a confusing 404 three API calls later.
#
# https only, deliberately: the API token travels in an HTTP Basic header on
# every request, and plaintext must never be an option. The domain is not
# hard-coded to atlassian.net so a custom Cloud domain still works. A trailing
# slash is accepted here and stripped by the validator below - a pattern can
# reject a value but cannot normalise one.
SITE_URL_PATTERN = r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]\.[A-Za-z]{2,}/?$"

# Jira Cloud project keys: 2-10 characters, uppercase, starting with a letter.
# This is also what makes JQL interpolation safe - the pattern leaves no
# character available to escape a quoted string - so keep the class narrow.
PROJECT_KEY_PATTERN = r"^[A-Z][A-Z0-9]{1,9}$"

# A shape check, not a deliverability check. Pydantic's EmailStr would need the
# email-validator package as a new runtime dependency for no real gain: Jira is
# the only thing that can say whether this address owns the token.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class JiraIngestRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    site_url: str = Field(
        pattern=SITE_URL_PATTERN,
        description="Your Jira Cloud site root, e.g. "
        '"https://your-company.atlassian.net". A trailing slash is accepted.',
        examples=["https://your-company.atlassian.net"],
    )
    email: str = Field(
        pattern=EMAIL_PATTERN,
        description="The Atlassian account email the API token belongs to. Used "
        "as the HTTP Basic username; never logged.",
        examples=["developer@example.com"],
    )
    api_token: SecretStr = Field(
        description="An Atlassian API token for that account, with read access "
        "to the project. Held in memory for this request only; never logged or "
        "stored.",
    )
    project_key: str = Field(
        pattern=PROJECT_KEY_PATTERN,
        description="The project to ingest, e.g. \"TRACK\". The JQL query is "
        "built from this; arbitrary JQL is not accepted.",
        examples=["TRACK"],
    )
    full: bool = Field(
        default=False,
        description="Return every issue and every chunk instead of a sample, "
        "with chunk contents left untruncated. The pipeline always processes "
        "the whole project either way - this only controls how much of the "
        "result is serialised.",
    )
    max_issues: int | None = Field(
        default=None,
        ge=1,
        description="Override how many issues this run will retrieve. Defaults "
        "to MAX_ISSUES_PER_INGESTION. If Jira has more results than this, the "
        "response is marked `truncated`.",
        examples=[200],
    )
    embed: bool = Field(
        default=True,
        description="Embed the chunks this run produces. Turn it off to fetch, "
        "normalise and chunk without spending embedding quota - the response is "
        "the same shape either way, with null vectors.",
    )
    include_embeddings: bool = Field(
        default=False,
        description="Return each chunk's complete vector instead of the first "
        "few values. 1536 floats per chunk adds up fast - expect a much larger "
        "response on a real project.",
    )

    @field_validator("site_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        """Make the site URL canonical.

        Not needed for correctness - httpx joins "https://x.atlassian.net/" and
        "/rest/api/3/myself" without doubling the slash either way. It matters
        because this value is echoed back in the response and written to the run
        header in the log, so the same site sent with and without a slash should
        not produce two different-looking runs.
        """
        return value.rstrip("/")
