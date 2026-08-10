"""The debug/verification response returned by the Slack ingest endpoint.

This response exists so a human can eyeball whether ingestion actually worked:
counts to check the funnel (retrieved -> parsed -> chunks), the normalised
messages themselves, a few sample chunks, and anything that was skipped.

Two different things can shorten what you see here, and they must not be
confused:

  truncated: true   the INGESTION stopped early. The channel held more history
                    than we fetched, because the message cap was reached. Every
                    count below describes only what was retrieved.

  full: false       the RESPONSE is a window onto a complete run. The message
                    and chunk lists are sampled and chunk text is shortened, but
                    every count is a true total and `truncated` is untouched.

Sampling never sets `truncated`.
"""

from pydantic import BaseModel

from app.models.slack_message import SlackMessage

# How much of the internal result is exposed. Raise these while debugging.
SAMPLE_MESSAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

# Sample chunks show the head of their text, not all of it. The SlackChunk
# objects themselves always keep the complete content - this cap applies only to
# what gets serialised into the HTTP response. Slack messages are short enough
# that this rarely bites; it is kept at the same value as the other sources so
# `full` behaves identically whichever endpoint you call.
CHUNK_CONTENT_PREVIEW_CHARS = 600

# Ceiling on how many history items one request will retrieve. Slack pages at
# 200 results per call, so this is under three calls rather than an unbounded
# walk of a busy channel. When the cap is hit, `truncated` is set.
MAX_MESSAGES_PER_INGESTION = 500


class SlackChunkSample(BaseModel):
    """One generated chunk, with its text possibly shortened for display."""

    channel_id: str
    message_ts: str
    author_id: str | None = None
    content: str


class SlackMessageError(BaseModel):
    """A history item that could not be read, or a note about the run, and why.

    Keyed by message timestamp where one message is at fault, by its position in
    the response where it has no timestamp to be named by, and by channel id for
    a note about the run as a whole. Neither is fatal - a single unreadable
    payload should not cost you the other 200 messages.

    Filtered messages are deliberately absent from this list. A channel-join
    notice or a thread reply being skipped is the pipeline working, not an
    error, and recording each one would bury the entries that mean something.
    """

    message: str
    reason: str


class SlackIngestResponse(BaseModel):
    """What the Slack ingest endpoint returns."""

    channel_id: str

    # The funnel. retrieved -> parsed -> chunks produced.
    #
    # Unlike the other three sources, `parsed_messages` being lower than
    # `retrieved_messages` is the NORMAL case here rather than a sign of
    # trouble: dropping thread replies, channel events and empty messages is
    # the parser's job, and a real channel is full of them. `generated_chunks`
    # does equal `parsed_messages` always, since one message makes one chunk.
    retrieved_messages: int
    parsed_messages: int
    generated_chunks: int

    # True when the message cap stopped the run before the channel ran out of
    # history, meaning this is a partial view of the channel. Not affected by
    # sampling.
    truncated: bool = False

    # The normalised messages, sampled unless `full` was requested. These are
    # the whole model rather than a summary: checking that every message carries
    # the channel you asked for, that no thread reply survived, and that text
    # arrived readable is the main reason to look at this response at all.
    messages: list[SlackMessage] = []

    sample_chunks: list[SlackChunkSample] = []
    errors: list[SlackMessageError] = []
