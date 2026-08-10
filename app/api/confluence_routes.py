"""HTTP routes for Confluence ingestion.

Contains: the route, and the projection from a ConfluenceIngestionResult onto
the JSON response. Never contains: Confluence calls, space resolution,
storage-format parsing, pagination or chunk formatting - those belong to the
connector, the service and the chunker respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.confluence_ingestion_service import (
    ConfluenceIngestionResult,
    ConfluenceIngestionService,
)
from app.models.confluence_request import ConfluenceIngestRequest
from app.models.confluence_response import (
    CHUNK_CONTENT_PREVIEW_CHARS,
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_PAGES_LIMIT,
    ConfluenceChunkSample,
    ConfluenceIngestResponse,
    ConfluencePageError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/confluence", tags=["confluence"])

_service = ConfluenceIngestionService()


def get_confluence_ingestion_service() -> ConfluenceIngestionService:
    """Supply the service to the route.

    A FastAPI dependency rather than a direct import so tests can override it
    with `app.dependency_overrides` instead of reaching into module globals.
    """
    return _service


@router.post(
    "/ingest",
    response_model=ConfluenceIngestResponse,
    summary="Ingest a Confluence space's pages into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised pages, and a sample of "
        "chunks for verification."
    ),
)
def ingest_space(
    request: ConfluenceIngestRequest,
    service: ConfluenceIngestionService = Depends(get_confluence_ingestion_service),
) -> ConfluenceIngestResponse:
    """Fetch, normalise and chunk one Confluence space.

    The response is a verification aid: it reports the full counts but only a
    sample of the pages and chunks, because a large space would otherwise return
    megabytes of text.
    """
    result = service.ingest(
        site_url=request.site_url,
        email=request.email,
        api_token=request.api_token,
        space_key=request.space_key,
        max_pages=request.max_pages,
    )
    return _to_response(result, full=request.full)


def _to_response(
    result: ConfluenceIngestionResult, *, full: bool = False
) -> ConfluenceIngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole space; `full` only decides how much
    of that result is serialised. It never affects `truncated`, which reports
    whether the *ingestion* saw everything.
    """
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
        pages=result.pages[:page_limit],
        sample_chunks=[
            ConfluenceChunkSample(
                page_id=chunk.page_id,
                space_key=chunk.space_key,
                title=chunk.title,
                parent_id=chunk.parent_id,
                status=chunk.status,
                content=chunk.content if full else _preview(chunk.content),
            )
            for chunk in result.chunks[:chunk_limit]
        ],
        errors=[
            ConfluencePageError(page=page, reason=reason)
            for page, reason in result.errors
        ],
    )


def _preview(content: str) -> str:
    """Shorten a chunk's text for display.

    Only the response is shortened - the ConfluenceChunk objects the pipeline
    produced still hold the complete text.
    """
    if len(content) <= CHUNK_CONTENT_PREVIEW_CHARS:
        return content
    return content[:CHUNK_CONTENT_PREVIEW_CHARS] + "\n... [truncated]"
