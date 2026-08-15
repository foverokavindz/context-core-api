"""The request body for POST /api/v1/slack/ingest."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# A Slack conversation id, checked loosely on purpose.
#
# Real ids start with C (public channel), G (private channel or legacy group) or
# D (direct message), and Slack has changed their length more than once - nine
# characters was normal, eleven is common now, and enterprise workspaces are
# longer still. Pinning the prefix or the length would reject valid channels for
# no gain, so this rejects only obvious nonsense - an empty string, a URL, a
# channel name with a leading "#" - and lets Slack make the real decision.
#
# It can afford to be this wide. Unlike Jira's project key, which is
# interpolated into a JQL string, this value is sent as a URL query parameter
# and percent-encoded by httpx, so there is nothing for it to escape out of.
CHANNEL_ID_PATTERN = r"^[A-Za-z0-9]{2,32}$"


class SlackIngestRequest(BaseModel):
    """A request to ingest the message history of one Slack channel.

    The token is a SecretStr rather than a plain str on purpose. Pydantic prints
    it as `**********` in every repr, log line and `model_dump()`, so it cannot
    leak through an accidental log statement or by being echoed into a response.
    `.get_secret_value()` is called in exactly one place - where the Slack HTTP
    client is constructed - and the value is never stored anywhere.

    Unknown fields are forbidden, for the same reason Jira and Confluence forbid
    them. The API deliberately does not accept a second channel, an
    `oldest`/`latest` time window, an `inclusive` flag or a thread timestamp;
    pydantic's default would silently ignore such a key rather than rejecting
    it, which is the wrong answer for a restriction that exists to keep a run
    inside the one channel the caller named.
    """

    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(
        description="A Slack bot token (xoxb-...) for an app installed in the "
        "workspace, with channels:history or groups:history. Held in memory "
        "for this request only; never logged or stored.",
    )
    channel_id: str = Field(
        pattern=CHANNEL_ID_PATTERN,
        description="The conversation to ingest, e.g. \"C0123456789\". This is "
        "the channel id, not the channel name - no channel lookup is performed, "
        "so \"#engineering\" will not work.",
        examples=["C0123456789"],
    )
    full: bool = Field(
        default=False,
        description="Return every message and every chunk instead of a sample, "
        "with chunk contents left untruncated. The pipeline always processes "
        "the whole run either way - this only controls how much of the result "
        "is serialised.",
    )
    max_messages: int | None = Field(
        default=None,
        ge=1,
        description="Override how many history items this run will retrieve. "
        "Defaults to MAX_MESSAGES_PER_INGESTION. Slack returns history newest "
        "first, so a capped run holds the most recent messages. If the channel "
        "has more, the response is marked `truncated`.",
        examples=[200],
    )
    embed: bool = Field(
        default=True,
        description="Embed the chunks this run produces. Turn it off to fetch, "
        "filter and chunk without spending embedding quota - the response is "
        "the same shape either way, with null vectors.",
    )
    include_embeddings: bool = Field(
        default=False,
        description="Return each chunk's complete vector instead of the first "
        "few values. A one-line message still carries 1536 floats, so this adds "
        "up faster here than anywhere else.",
    )
