"""Orchestration for Slack ingestion: fetch, parse, chunk.

    SlackConnector -> raw JSON -> SlackParser -> SlackMessage[] -> SlackChunk[]

This module owns the order of the pipeline and nothing else. It contains no HTTP
calls, no knowledge of Slack's field names, and no chunk formatting - those
belong to the connector, the parser and the chunker respectively.

There is no linking pass and no second fetch, which is what keeps it shorter than
the Jira service. Jira needs a linking pass because it tells you a Story's parent
but never an Epic's children. Slack's equivalent temptation is threads - a
message that was replied to knows nothing about its replies - and the answer here
is not to resolve it: nothing calls conversations.replies, so a thread root is
ingested as the ordinary channel message it is and its replies are simply not
part of this version.
"""

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

    Holds *every* message and *every* chunk. The HTTP layer serialises only a
    sample of this, but the full set is what a later phase would hand to an
    embedding step - which is the whole point of keeping it intact here.

    `retrieved_messages` counts raw history items as Slack served them;
    `parsed_messages` counts what survived the filter. The gap between the two
    is thread replies and channel events, and it is expected to be large - which
    is the one way reading this result differs from reading the other three
    sources'.
    """

    channel_id: str

    retrieved_messages: int = 0
    truncated: bool = False

    messages: list[SlackMessage] = field(default_factory=list)
    chunks: list[SlackChunk] = field(default_factory=list)

    errors: list[tuple[str, str]] = field(default_factory=list)

    # What the embedding step did, or zeroes when it was skipped. The vectors
    # themselves live on the chunks above - there is one list of chunks in a run
    # and it is the one the chunker produced.
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
        # No default embedder on purpose. A service built without one parses and
        # chunks and stops there, which is what a test wants; the API wires a
        # real one in. An embedder constructed by mistake would be the kind of
        # default that quietly bills somebody.
        self.embedder = embedder

    def ingest(
        self,
        token: SecretStr,
        channel_id: str,
        max_messages: int | None = None,
        embed: bool = True,
    ) -> SlackIngestionResult:
        """Ingest one Slack channel and return everything that was produced.

        `max_messages` overrides the service default for this run only. `embed`
        turns the embedding step off for a run that only wants chunks; it does
        nothing when the service was built without an embedder.

        Raises IngestionError subclasses for whole-run failures - a rejected
        token, a channel the bot is not in, a rate limit, and a failed embedding
        pass. Problems with individual messages are collected into `errors` and
        do not stop the run, and messages the parser filters out are not
        problems at all.
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

        # The channel comes from the snapshot, not from what the caller typed:
        # every message in the result is stamped with the channel that was
        # actually read, so the response cannot claim a channel the run did not
        # touch.
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

        # The shared stage: identical for all four sources, so it lives beside
        # the embedder rather than being written out four times.
        embed_into(result, self.embedder, embed=embed)

        return result
