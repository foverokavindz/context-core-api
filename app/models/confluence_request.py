"""The request body for POST /api/v1/confluence/ingest."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# A Confluence Cloud site root, and nothing more. The same pattern the Jira
# request uses, and for the same reasons: anchored so a page URL or a bare
# hostname is rejected up front, https only because the API token travels in an
# HTTP Basic header, and not hard-coded to atlassian.net so a custom Cloud
# domain still works. A trailing slash is accepted here and stripped below.
#
# Note this is the site root, not the /wiki path. The connector adds /wiki
# itself, so a caller never has to know that Confluence lives under one.
SITE_URL_PATTERN = r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]\.[A-Za-z]{2,}/?$"

# Confluence space keys are nothing like Jira project keys, so this pattern is
# deliberately NOT PROJECT_KEY_PATTERN. Real keys on a real site include "TR",
# a single-character "F", and personal spaces keyed "~<account id>" - all of
# which [A-Z][A-Z0-9]{1,9} would reject.
#
# Being wider costs nothing here. The key is sent as a URL query parameter,
# which httpx percent-encodes, so unlike Jira's JQL interpolation there is no
# string for it to escape out of. The class still excludes "/", "?", "#",
# quotes and whitespace, so it cannot reshape the request path either.
SPACE_KEY_PATTERN = r"^[A-Za-z0-9~._-]{1,255}$"

# A shape check, not a deliverability check - see JiraIngestRequest.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ConfluenceIngestRequest(BaseModel):
    """A request to ingest the pages of one Confluence space.

    The token is a SecretStr rather than a plain str on purpose. Pydantic prints
    it as `**********` in every repr, log line and `model_dump()`, so it cannot
    leak through an accidental log statement or by being echoed into a response.
    `.get_secret_value()` is called in exactly one place - where the Confluence
    HTTP client is constructed - and the value is never stored anywhere.

    Unknown fields are forbidden. The API deliberately does not accept a CQL
    query, a body format or a page-status filter from the caller; pydantic's
    default would silently ignore such a key rather than rejecting it, which is
    the wrong answer for restrictions that exist to keep a run inside one space.
    """

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
