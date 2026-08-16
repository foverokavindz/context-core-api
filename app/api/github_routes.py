"""HTTP routes for GitHub ingestion.

Contains: the route, and the projection from an IngestionResult onto the JSON
response. Never contains: GitHub calls, filtering rules, or parsing - those
belong to the connector, the filter and the parsers respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.ingestion_service import GitHubIngestionService, IngestionResult
from app.models.embedding_counts import EmbeddingCounts
from app.models.github_request import GitHubIngestRequest
from app.models.ingest_response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_FILES_LIMIT,
    FileError,
    FileSummary,
    IngestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["github"])

# The embedder reads no environment variable and opens no connection until the
# first batch is sent, so building one here costs nothing and importing this
# module on a machine with no credentials still works.
_service = GitHubIngestionService(embedder=ChunkEmbedder())

def get_ingestion_service() -> GitHubIngestionService:
    """Supply the service to the route.

    A FastAPI dependency rather than a direct import so tests can override it
    with `app.dependency_overrides` instead of reaching into module globals.
    """
    return _service


@router.post( "/ingest", response_model=IngestResponse, summary="Ingest a GitHub repository into embedded code chunks",
    response_description=(
        "Counts for each pipeline stage, plus a sample of files and chunks - "
        "each with its vector - for verification."
    ),
)
def ingest_repository(request: GitHubIngestRequest, service: GitHubIngestionService = Depends(get_ingestion_service),) -> IngestResponse:
    """Fetch, filter, parse, chunk and embed one repository branch.

    The response is a verification aid: it reports the full counts but only a
    sample of the files and chunks, because a real repository would otherwise
    return megabytes of source - and, with vectors, tens of megabytes.
    """
    result = service.ingest(
        token=request.token,
        repository=request.repository,
        branch=request.branch,
        max_files=request.max_files,
        embed=request.embed,
    )
    return to_response(result, full=request.full)


def to_response(result: IngestionResult, *, full: bool = False) -> IngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole repository; `full` only decides how
    many of the files and chunks are serialised. Sampled by default because a
    real repository produces hundreds of chunks and the payload gets unwieldy
    fast - but whatever is serialised is serialised whole, contents and vectors
    included.

    Public rather than private because the background pipeline serialises its
    run through this same projection - one definition of what a GitHub run looks
    like as JSON, whether it arrived over HTTP or was written to a file.
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
        counts=EmbeddingCounts(
            chunks=result.generated_chunks,
            embeddings=result.embedded_chunks,
            embedding_batches=result.embedding_batches,
            embedding_model=result.embedding_model,
            embedding_dimensions=result.embedding_dimensions,
            truncated_inputs=result.embedding_truncated_inputs,
        ),
        resource_files=[
            FileSummary(
                repository=file.repository,
                branch=file.branch,
                commit_sha=file.commit_sha,
                path=file.path,
                file_name=file.file_name,
                extension=file.extension,
                file_sha=file.file_sha,
                language=file.language,
                size=file.size,
                team_id=file.team_id,
                department_id=file.department_id,
                access_scope=file.access_scope,
            )
            for file in result.files[:file_limit]
        ],
        chunks=result.chunks[:chunk_limit],
        errors=[FileError(file=path, reason=reason) for path, reason in result.errors],
    )
