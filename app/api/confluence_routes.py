import logging

from fastapi import APIRouter, Depends

from app.ingestion.confluence_ingestion_service import (
    ConfluenceIngestionResult,
    ConfluenceIngestionService,
)
from app.ingestion.embedding_service import ChunkEmbedder
from app.models.confluence.request import ConfluenceIngestRequest
from app.models.confluence.response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_PAGES_LIMIT,
    ConfluenceIngestResponse,
    ConfluencePageError,
)
from app.models.common.api_response import ApiResponse
from app.models.common.embedding_counts import EmbeddingCounts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/confluence", tags=["confluence"])

_service = ConfluenceIngestionService(embedder=ChunkEmbedder())


def get_confluence_ingestion_service() -> ConfluenceIngestionService:

    return _service


@router.post(
    "/ingest",
    response_model=ApiResponse[ConfluenceIngestResponse],
    summary="Ingest a Confluence space's pages into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised pages, and a sample of "
        "chunks for verification."
    ),
)
def ingest_space(
    request: ConfluenceIngestRequest,
    service: ConfluenceIngestionService = Depends(get_confluence_ingestion_service),
) -> ApiResponse[ConfluenceIngestResponse]:

    result = service.ingest(
        site_url=request.site_url,
        email=request.email,
        api_token=request.api_token,
        space_key=request.space_key,
        max_pages=request.max_pages,
        embed=request.embed,
    )
    return ApiResponse[ConfluenceIngestResponse].ok(
        to_response(result, full=request.full)
    )


def to_response(
    result: ConfluenceIngestionResult, *, full: bool = False
) -> ConfluenceIngestResponse:

    page_limit = None if full else SAMPLE_PAGES_LIMIT
    chunk_limit = None if full else SAMPLE_CHUNKS_LIMIT

    return ConfluenceIngestResponse(
        site_url=result.site_url,
        space_key=result.space_key,
        space_id=result.space_id,
        space_name=result.space_name,
        retrieved_pages=result.retrieved_pages,
        parsed_pages=result.parsed_pages,
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
        resource_files=result.pages[:page_limit],
        chunks=result.chunks[:chunk_limit],
        errors=[
            ConfluencePageError(page=page, reason=reason)
            for page, reason in result.errors
        ],
    )
