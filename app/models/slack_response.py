from pydantic import BaseModel

from app.models.embedding_counts import EmbeddingCounts
from app.models.slack_chunk import SlackChunk
from app.models.slack_message import SlackMessage

SAMPLE_MESSAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

MAX_MESSAGES_PER_INGESTION = 500


class SlackMessageError(BaseModel):

    message: str
    reason: str


class SlackIngestResponse(BaseModel):
    """What the Slack ingest endpoint returns."""

    channel_id: str

    retrieved_messages: int
    parsed_messages: int
    generated_chunks: int

    truncated: bool = False

    counts: EmbeddingCounts

    resource_files: list[SlackMessage] = []

    sample_chunks: list[SlackChunk] = []
    errors: list[SlackMessageError] = []
