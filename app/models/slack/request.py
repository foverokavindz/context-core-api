"""The request body for POST /api/v1/slack/ingest."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr

CHANNEL_ID_PATTERN = r"^[A-Za-z0-9]{2,32}$"

class SlackIngestRequest(BaseModel):
    """A request to ingest the message history of one Slack channel.
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
