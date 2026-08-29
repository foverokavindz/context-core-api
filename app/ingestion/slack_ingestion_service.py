
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.connectors.slack_connector import SlackConnector
from app.ingestion.embedding_service import ChunkEmbedder, embed_into
from app.ingestion.slack_chunker import SlackChunker
from app.ingestion.slack_parser import SlackParser
from app.models.slack.chunk import SlackChunk
from app.models.slack.message import SlackMessage
from app.models.slack.response import MAX_MESSAGES_PER_INGESTION

logger = logging.getLogger(__name__)

# Builds the connector for a run. Injectable so tests can substitute a double
# without patching module internals or needing a real Slack workspace.
SlackConnectorFactory = Callable[[SecretStr], SlackConnector]


@dataclass
class SlackIngestionResult:
    """The complete outcome of one run.
    """

    channel_id: str

    retrieved_messages: int = 0
    truncated: bool = False

    messages: list[SlackMessage] = field(default_factory=list)
    chunks: list[SlackChunk] = field(default_factory=list)

    errors: list[tuple[str, str]] = field(default_factory=list)
    embedded_chunks: int = 0
    embedding_batches: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_truncated_inputs: int = 0

    @property
    def parsed_messages(self) -> int:
        return len(self.messages)

    @property
    def generated_chunks(self) -> int:
        return len(self.chunks)


class SlackIngestionService:
    """Runs the Slack pipeline end to end for one channel."""

    def __init__(
        self,
        *,
        parser: SlackParser | None = None,
        chunker: SlackChunker | None = None,
        connector_factory: SlackConnectorFactory = SlackConnector,
        max_messages: int = MAX_MESSAGES_PER_INGESTION,
        embedder: ChunkEmbedder | None = None,
    ) -> None:
        self.parser = parser or SlackParser()
        self.chunker = chunker or SlackChunker()
        self.connector_factory = connector_factory
        self.max_messages = max_messages
        self.embedder = embedder

    def ingest(
        self,
        token: SecretStr,
        channel_id: str,
        max_messages: int | None = None,
        embed: bool = True,
    ) -> SlackIngestionResult:
        """Ingest one Slack channel and return everything that was produced.
        """
        logger.info("Ingesting Slack channel %s", channel_id)
        started = time.monotonic()

        # The connector holds the caller's token, so it is closed as soon as the
        # fetching is done - parsing and chunking happen afterwards, without it.
        with self.connector_factory(token) as connector:
            snapshot = connector.get_history(
                channel_id,
                max_messages=(
                    self.max_messages if max_messages is None else max_messages
                ),
            )

        result = SlackIngestionResult(
            channel_id=snapshot.channel_id,
            retrieved_messages=snapshot.retrieved_messages,
            truncated=snapshot.truncated,
            errors=list(snapshot.errors),
        )

        result.messages = self.parser.parse_many(
            snapshot.messages,
            result.errors,
            channel_id=snapshot.channel_id,
        )
        result.chunks = self.chunker.chunk_many(result.messages)

        logger.info(
            "Ingested Slack channel %s (%d history items retrieved, %d parsed) "
            "into %d chunks in %.1fs",
            result.channel_id,
            result.retrieved_messages,
            result.parsed_messages,
            result.generated_chunks,
            time.monotonic() - started,
        )

        embed_into(result, self.embedder, embed=embed)

        return result
