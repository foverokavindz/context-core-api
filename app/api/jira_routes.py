"""HTTP routes for Jira ingestion.

Contains: the route, and the projection from a JiraIngestionResult onto the JSON
response. Never contains: Jira calls, ADF parsing, relationship resolution or
chunk formatting - those belong to the connector, the parser, the service and
the chunker respectively.
"""

import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.jira_ingestion_service import (
    JiraIngestionResult,
    JiraIngestionService,
)
from app.models.embedding_counts import EmbeddingCounts
from app.models.jira_request import JiraIngestRequest
from app.models.jira_response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_ISSUES_LIMIT,
    JiraIngestResponse,
    JiraIssueError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jira", tags=["jira"])

# The embedder reads no environment variable and opens no connection until the
# first batch is sent, so building one here costs nothing and importing this
# module on a machine with no credentials still works.
_service = JiraIngestionService(embedder=ChunkEmbedder())


def get_jira_ingestion_service() -> JiraIngestionService:
    """Supply the service to the route.

    A FastAPI dependency rather than a direct import so tests can override it
    with `app.dependency_overrides` instead of reaching into module globals.
    """
    return _service


@router.post(
    "/ingest",
    response_model=JiraIngestResponse,
    summary="Ingest a Jira project's Epics and Stories into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised issues, and a sample of "
        "chunks for verification."
    ),
)
def ingest_project(
    request: JiraIngestRequest,
    service: JiraIngestionService = Depends(get_jira_ingestion_service),
) -> JiraIngestResponse:
    """Fetch, normalise, link and chunk one Jira project.

    The response is a verification aid: it reports the full counts but only a
    sample of the issues and chunks, because a large project would otherwise
    return megabytes of text.
    """
    result = service.ingest(
        site_url=request.site_url,
        email=request.email,
        api_token=request.api_token,
        project_key=request.project_key,
        max_issues=request.max_issues,
        embed=request.embed,
    )
    return to_response(result, full=request.full)


def to_response(
    result: JiraIngestionResult, *, full: bool = False
) -> JiraIngestResponse:
    """Project the internal result onto the HTTP response.

    The pipeline always processes the whole project; `full` only decides how
    many of the issues and chunks are serialised, and does not affect
    `truncated`, which reports whether the *ingestion* saw everything.

    Public rather than private because the background pipeline serialises its
    run through this same projection.
    """
    issue_limit = None if full else SAMPLE_ISSUES_LIMIT
    chunk_limit = None if full else SAMPLE_CHUNKS_LIMIT

    return JiraIngestResponse(
        site_url=result.site_url,
        project_key=result.project_key,
        retrieved_issues=result.retrieved_issues,
        epics=result.epics,
        stories=result.stories,
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
        resource_files=result.issues[:issue_limit],
        chunks=result.chunks[:chunk_limit],
        errors=[
            JiraIssueError(issue=key, reason=reason) for key, reason in result.errors
        ],
    )
