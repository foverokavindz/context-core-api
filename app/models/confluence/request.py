"""The request body for POST /api/v1/confluence/ingest."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

SITE_URL_PATTERN = r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]\.[A-Za-z]{2,}/?$"

SPACE_KEY_PATTERN = r"^[A-Za-z0-9~._-]{1,255}$"

# A shape check, not a deliverability check - see JiraIngestRequest.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ConfluenceIngestRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    site_url: str = Field(
        pattern=SITE_URL_PATTERN,
        description="Your Atlassian Cloud site root, e.g. "
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
        "to the space. A scoped token needs read:space:confluence and "
        "read:page:confluence. Held in memory for this request only; never "
        "logged or stored.",
    )
    space_key: str = Field(
        pattern=SPACE_KEY_PATTERN,
        description='The space to ingest, e.g. "TR". Only pages belonging to '
        "this space are retrieved; the key is resolved to a space id and every "
        "page request is filtered by it.",
        examples=["TR"],
    )
    full: bool = Field(
        default=False,
        description="Return every page and every chunk instead of a sample, "
        "with chunk contents left untruncated. The pipeline always processes "
        "the whole space either way - this only controls how much of the "
        "result is serialised.",
    )
    max_pages: int | None = Field(
        default=None,
        ge=1,
        description="Override how many pages this run will retrieve. Defaults "
        "to MAX_PAGES_PER_INGESTION. If the space holds more pages than this, "
        "the response is marked `truncated`.",
        examples=[200],
    )
    embed: bool = Field(
        default=True,
        description="Embed the chunks this run produces. Turn it off to fetch, "
        "flatten and chunk without spending embedding quota - the response is "
        "the same shape either way, with null vectors.",
    )

    @field_validator("site_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        """Make the site URL canonical.

        Not needed for correctness - httpx joins the site and a relative path
        without doubling the slash either way. It matters because this value is
        echoed back in the response and written to the run header in the log, so
        the same site sent with and without a slash should not produce two
        different-looking runs.
        """
        return value.rstrip("/")
