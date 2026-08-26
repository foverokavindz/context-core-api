import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.slack_ingestion_service import (
    SlackIngestionResult,
    SlackIngestionService,
)
from app.models.common.api_response import ApiResponse
from app.models.common.embedding_counts import EmbeddingCounts
from app.models.slack.request import SlackIngestRequest
from app.models.slack.response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_MESSAGES_LIMIT,
    SlackIngestResponse,
    SlackMessageError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/slack", tags=["slack"])

_service = SlackIngestionService(embedder=ChunkEmbedder())


def get_slack_ingestion_service() -> SlackIngestionService:

    return _service


@router.post(
    "/ingest",
    response_model=ApiResponse[SlackIngestResponse],
    summary="Ingest a Slack channel's message history into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised messages, and a sample "
        "of chunks for verification."
    ),
)
def ingest_channel(
    request: SlackIngestRequest,
    service: SlackIngestionService = Depends(get_slack_ingestion_service),
) -> ApiResponse[SlackIngestResponse]:

    result = service.ingest(
        token=request.token,
        channel_id=request.channel_id,
        max_messages=request.max_messages,
        embed=request.embed,
    )
    return ApiResponse[SlackIngestResponse].ok(
        to_response(result, full=request.full)
    )


def to_response(
    result: SlackIngestionResult, *, full: bool = False
) -> SlackIngestResponse:

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
        chunks=result.chunks[:chunk_limit],
        errors=[
            SlackMessageError(message=subject, reason=reason)
            for subject, reason in result.errors
        ],
    )
