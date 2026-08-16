"""HTTP routes for Slack ingestion.

Contains: the route, and the projection from a SlackIngestionResult onto the JSON
response. Never contains: Slack calls, cursor pagination, message filtering or
chunk formatting - those belong to the connector, the parser and the chunker
respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.slack_ingestion_service import (
    SlackIngestionResult,
    SlackIngestionService,
)
from app.models.embedding_counts import EmbeddingCounts
from app.models.ingest_response import EMBEDDING_PREVIEW_VALUES
from app.models.slack_chunk import SlackChunk
from app.models.slack_request import SlackIngestRequest
from app.models.slack_response import (
    CHUNK_CONTENT_PREVIEW_CHARS,
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_MESSAGES_LIMIT,
    SlackChunkSample,
    SlackIngestResponse,
    SlackMessageError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/slack", tags=["slack"])

# The embedder reads no environment variable and opens no connection until the
# first batch is sent, so building one here costs nothing and importing this
# module on a machine with no credentials still works.
_service = SlackIngestionService(embedder=ChunkEmbedder())


def get_slack_ingestion_service() -> SlackIngestionService:
    """Supply the service to the route.

    A FastAPI dependency rather than a direct import so tests can override it
    with `app.dependency_overrides` instead of reaching into module globals.
    """
    return _service


@router.post(
    "/ingest",
    response_model=SlackIngestResponse,
    summary="Ingest a Slack channel's message history into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised messages, and a sample "
        "of chunks for verification."
    ),
)
def ingest_channel(
    request: SlackIngestRequest,
    service: SlackIngestionService = Depends(get_slack_ingestion_service),
) -> SlackIngestResponse:
    """Fetch, filter and chunk one Slack channel's history.

    The response is a verification aid: it reports the full counts but only a
    sample of the messages and chunks, because a busy channel would otherwise
    return megabytes of conversation.
    """
    result = service.ingest(
        token=request.token,
        channel_id=request.channel_id,
        max_messages=request.max_messages,
        embed=request.embed,
    )
    return to_response(
        result, full=request.full, include_embeddings=request.include_embeddings
    )


def to_response(
    result: SlackIngestionResult,
    *,
    full: bool = False,
    include_embeddings: bool = False,
) -> SlackIngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole run; `full` only decides how much of
    that result is serialised, and `include_embeddings` whether a chunk's vector
    is shown whole or as its first few values. Neither affects `truncated`,
    which reports whether the *ingestion* saw the whole channel.

    Public rather than private because the background pipeline serialises its
    run through this same projection.
    """
    message_limit = None if full else SAMPLE_MESSAGES_LIMIT
    chunk_limit = None if full else SAMPLE_CHUNKS_LIMIT

    return SlackIngestResponse(
        channel_id=result.channel_id,
        retrieved_messages=result.retrieved_messages,
        parsed_messages=result.parsed_messages,
        generated_chunks=result.generated_chunks,
        truncated=result.truncated,
        counts=EmbeddingCounts(
            chunks=result.generated_chunks,
            embeddings=result.embedded_chunks,
            embedding_batches=result.embedding_batches,
            embedding_model=result.embedding_model,
            embedding_dimensions=result.embedding_dimensions,
            truncated_inputs=result.embedding_truncated_inputs,
        ),
        resource_files=result.messages[:message_limit],
        sample_chunks=[
            _to_sample(chunk, full=full, include_embeddings=include_embeddings)
            for chunk in result.chunks[:chunk_limit]
        ],
        errors=[
            SlackMessageError(message=subject, reason=reason)
            for subject, reason in result.errors
        ],
    )


def _to_sample(
    chunk: SlackChunk, *, full: bool, include_embeddings: bool
) -> SlackChunkSample:
    """Project one chunk, shortening its text and its vector for display."""
    return SlackChunkSample(
        channel_id=chunk.channel_id,
        message_ts=chunk.message_ts,
        author_id=chunk.author_id,
        content=chunk.content if full else _preview(chunk.content),
        embedding=chunk.embedding if include_embeddings else None,
        embedding_preview=(
            None
            if chunk.embedding is None or include_embeddings
            else chunk.embedding[:EMBEDDING_PREVIEW_VALUES]
        ),
        # Taken from the vector in hand rather than from the run's summary, so a
        # chunk that somehow missed the embedding pass reads as null here
        # instead of borrowing the width of the ones that did not.
        embedding_dimensions=None if chunk.embedding is None else len(chunk.embedding),
        embedding_model=chunk.embedding_model,
        team_id=chunk.team_id,
        department_id=chunk.department_id,
        access_scope=chunk.access_scope,
    )


def _preview(content: str) -> str:
    """Shorten a chunk's text for display.

    Only the response is shortened - the SlackChunk objects the pipeline
    produced still hold the complete text.
    """
    if len(content) <= CHUNK_CONTENT_PREVIEW_CHARS:
        return content
    return content[:CHUNK_CONTENT_PREVIEW_CHARS] + "\n... [truncated]"
