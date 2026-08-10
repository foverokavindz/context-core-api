"""HTTP routes for GitHub ingestion.

Contains: the route, and the projection from an IngestionResult onto the JSON
response. Never contains: GitHub calls, filtering rules, or parsing - those
belong to the connector, the filter and the parsers respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.ingestion_service import GitHubIngestionService, IngestionResult
from app.models.github_request import GitHubIngestRequest
from app.models.ingest_response import (
    CHUNK_CONTENT_PREVIEW_CHARS,
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_FILES_LIMIT,
    ChunkSample,
    FileError,
    FileSummary,
    IngestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["github"])

_service = GitHubIngestionService()

def get_ingestion_service() -> GitHubIngestionService:
    """Supply the service to the route.

    A FastAPI dependency rather than a direct import so tests can override it
    with `app.dependency_overrides` instead of reaching into module globals.
    """
    return _service


@router.post( "/ingest", response_model=IngestResponse, summary="Ingest a GitHub repository into code chunks",
    response_description=(
        "Counts for each pipeline stage, plus a sample of files and chunks for "
        "verification."
    ),
)
def ingest_repository(request: GitHubIngestRequest, service: GitHubIngestionService = Depends(get_ingestion_service),) -> IngestResponse:
    """Fetch, filter, parse and chunk one repository branch.

    The response is a verification aid: it reports the full counts but only a
    sample of the files and chunks, because a real repository would otherwise
    return megabytes of source.
    """
    result = service.ingest(
        token=request.token,
        repository=request.repository,
        branch=request.branch,
        max_files=request.max_files,
    )
    return _to_response(result, full=request.full)


def _to_response(result: IngestionResult, *, full: bool = False) -> IngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole repository; `full` only decides how
    much of that result is serialised. Sampled by default because a real
    repository produces hundreds of chunks and the payload gets unwieldy fast.
    """
    file_limit = None if full else SAMPLE_FILES_LIMIT
    chunk_limit = None if full else SAMPLE_CHUNKS_LIMIT

    return IngestResponse(
        repository=result.repository,
        branch=result.branch,
        commit_sha=result.commit_sha,
        discovered_files=result.discovered_files,
        accepted_files=result.accepted_files,
        parsed_files=result.parsed_files,
        generated_chunks=result.generated_chunks,
        truncated=result.truncated,
        files=[
            FileSummary(path=file.path, language=file.language, size=file.size)
            for file in result.files[:file_limit]
        ],
        sample_chunks=[
            ChunkSample(
                file_path=chunk.file_path,
                symbol_type=chunk.symbol_type,
                symbol_name=chunk.symbol_name,
                parent_symbol=chunk.parent_symbol,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content if full else _preview(chunk.content),
            )
            for chunk in result.chunks[:chunk_limit]
        ],
        errors=[FileError(file=path, reason=reason) for path, reason in result.errors],
    )


def _preview(content: str) -> str:
    """Shorten a chunk's source for display.

    Only the response is shortened - the CodeChunk objects the pipeline produced
    still hold the complete span.
    """
    if len(content) <= CHUNK_CONTENT_PREVIEW_CHARS:
        return content
    return content[:CHUNK_CONTENT_PREVIEW_CHARS] + "\n... [truncated]"
