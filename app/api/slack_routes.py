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
from app.models.slack_request import SlackIngestRequest
from app.models.slack_response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_MESSAGES_LIMIT,
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
    return to_response(result, full=request.full)


def to_response(
    result: SlackIngestionResult, *, full: bool = False
) -> SlackIngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole run; `full` only decides how many of
    the messages and chunks are serialised, and does not affect `truncated`,
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
        sample_chunks=result.chunks[:chunk_limit],
        errors=[
            SlackMessageError(message=subject, reason=reason)
            for subject, reason in result.errors
        ],
    )
