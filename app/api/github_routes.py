"""HTTP routes for GitHub ingestion.

Contains: the route, and the projection from an IngestionResult onto the JSON
response. Never contains: GitHub calls, filtering rules, or parsing - those
belong to the connector, the filter and the parsers respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.ingestion_service import GitHubIngestionService, IngestionResult
from app.models.code_chunk import CodeChunk
from app.models.embedding_counts import EmbeddingCounts
from app.models.github_request import GitHubIngestRequest
from app.models.ingest_response import (
    CHUNK_CONTENT_PREVIEW_CHARS,
    EMBEDDING_PREVIEW_VALUES,
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_FILES_LIMIT,
    ChunkSample,
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
    return _to_response(
        result, full=request.full, include_embeddings=request.include_embeddings
    )


def _to_response(
    result: IngestionResult,
    *,
    full: bool = False,
    include_embeddings: bool = False,
) -> IngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole repository; `full` only decides how
    much of that result is serialised, and `include_embeddings` whether a
    chunk's vector is shown whole or as its first few values. Sampled by default
    because a real repository produces hundreds of chunks and the payload gets
    unwieldy fast.
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
        files=[
            FileSummary(path=file.path, language=file.language, size=file.size)
            for file in result.files[:file_limit]
        ],
        chunks=[
            _to_sample(chunk, full=full, include_embeddings=include_embeddings)
            for chunk in result.chunks[:chunk_limit]
        ],
        errors=[FileError(file=path, reason=reason) for path, reason in result.errors],
    )


def _to_sample(
    chunk: CodeChunk, *, full: bool, include_embeddings: bool
) -> ChunkSample:
    """Project one chunk, shortening its source and its vector for display."""
    return ChunkSample(
        file_path=chunk.file_path,
        symbol_type=chunk.symbol_type,
        symbol_name=chunk.symbol_name,
        parent_symbol=chunk.parent_symbol,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
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
    )


def _preview(content: str) -> str:
    """Shorten a chunk's source for display.

    Only the response is shortened - the CodeChunk objects the pipeline produced
    still hold the complete span.
    """
    if len(content) <= CHUNK_CONTENT_PREVIEW_CHARS:
        return content
    return content[:CHUNK_CONTENT_PREVIEW_CHARS] + "\n... [truncated]"
