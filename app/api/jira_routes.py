import logging

from fastapi import APIRouter, Depends

from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.jira_ingestion_service import (
    JiraIngestionResult,
    JiraIngestionService,
)
from app.models.common.api_response import ApiResponse
from app.models.common.embedding_counts import EmbeddingCounts
from app.models.jira.request import JiraIngestRequest
from app.models.jira.response import (
    SAMPLE_CHUNKS_LIMIT,
    SAMPLE_ISSUES_LIMIT,
    JiraIngestResponse,
    JiraIssueError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jira", tags=["jira"])

_service = JiraIngestionService(embedder=ChunkEmbedder())


def get_jira_ingestion_service() -> JiraIngestionService:

    return _service


@router.post(
    "/ingest",
    response_model=ApiResponse[JiraIngestResponse],
    summary="Ingest a Jira project's Epics and Stories into chunks",
    response_description=(
        "Counts for each pipeline stage, the normalised issues, and a sample of "
        "chunks for verification."
    ),
)
def ingest_project(
    request: JiraIngestRequest,
    service: JiraIngestionService = Depends(get_jira_ingestion_service),
) -> ApiResponse[JiraIngestResponse]:
 
    result = service.ingest(
        site_url=request.site_url,
        email=request.email,
        api_token=request.api_token,
        project_key=request.project_key,
        max_issues=request.max_issues,
        embed=request.embed,
    )
    return ApiResponse[JiraIngestResponse].ok(
        to_response(result, full=request.full)
    )


def to_response(
    result: JiraIngestionResult, *, full: bool = False
) -> JiraIngestResponse:

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
