from pydantic import BaseModel

from app.models.embedding_counts import EmbeddingCounts
from app.models.permission_scope import PermissionScope
from app.models.slack_message import SlackMessage

SAMPLE_MESSAGES_LIMIT = 10
SAMPLE_CHUNKS_LIMIT = 20

CHUNK_CONTENT_PREVIEW_CHARS = 600

MAX_MESSAGES_PER_INGESTION = 500


class SlackChunkSample(PermissionScope):
    """One generated chunk, with its text possibly shortened for display."""

    channel_id: str
    message_ts: str
    author_id: str | None = None
    content: str

    embedding: list[float] | None = None
    embedding_preview: list[float] | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None


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

    sample_chunks: list[SlackChunkSample] = []
    errors: list[SlackMessageError] = []
