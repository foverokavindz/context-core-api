import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.api import confluence_routes, github_routes, jira_routes, slack_routes
from app.core.exceptions import IngestionError
from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_type import SourceType
from app.ingestion.confluence_ingestion_service import ConfluenceIngestionService
from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.ingestion_service import GitHubIngestionService
from app.ingestion.jira_ingestion_service import JiraIngestionService
from app.ingestion.slack_ingestion_service import SlackIngestionService
from app.models.ingest_data_request import IngestDataRequest
from app.models.permission_scope import PermissionScope

logger = logging.getLogger(__name__)

RUNS_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "runs"
RUN_FILE_FULL = True


def run_ingestion_pipeline(source: ExternalDataSource, request: IngestDataRequest) -> None:
    """Ingest from one connected source and write the run to a file."""

    started_at = _now()
    logger.info(
        "Ingestion run starting for %s source %s (%s)",
        source.source_type.value,
        source.id,
        source.name,
    )

    try:
        result, items, to_response = _ingest(source, request)

    except IngestionError as exc:
        logger.error(
            "Ingestion run failed for %s source %s: %s",
            source.source_type.value,
            source.id,
            type(exc).__name__,
        )
        _write_run(source, _failed(source, request, started_at, exc))
        return

    _apply_source_context(items, source, request)
    _apply_source_context(result.chunks, source, request)

    response = to_response(result, full=RUN_FILE_FULL)

    logger.info(
        "Ingestion run finished for %s source %s: %d resource files, %d chunks",
        source.source_type.value,
        source.id,
        len(items),
        len(result.chunks),
    )

    #TODO: run persistence service to persist the result and chunks to the database
    _write_run(
        source,
        {
            "source": _source_record(source, request),
            "status": "COMPLETED",
            "started_at": started_at,
            "completed_at": _now(),
            "result": response.model_dump(mode="json"),
        },
    )


def _ingest( source: ExternalDataSource, request: IngestDataRequest) -> tuple[Any, list[Any], Callable[..., Any]]:

    config = source.config or {}
    token = request.token

    #TODO: Call existing ingestion services - Need refactor later
    if source.source_type is SourceType.GITHUB:
        result = GitHubIngestionService(embedder=ChunkEmbedder()).ingest(
            token=token,
            repository=config["repository"],
            branch=config.get("branch"),
        )
        return result, result.files, github_routes.to_response #TODO : refactor to return the response model
 
    if source.source_type is SourceType.JIRA:
        result = JiraIngestionService(embedder=ChunkEmbedder()).ingest(
            site_url=config["site_url"],
            email=config["email"],
            api_token=token,
            project_key=config["project_key"],
        )
        return result, result.issues, jira_routes.to_response #TODO : refactor to return the response model

    if source.source_type is SourceType.CONFLUENCE:
        result = ConfluenceIngestionService(embedder=ChunkEmbedder()).ingest(
            site_url=config["site_url"],
            email=config["email"],
            api_token=token,
            space_key=config["space_key"],
        )
        return result, result.pages, confluence_routes.to_response #TODO : refactor to return the response model

    if source.source_type is SourceType.SLACK:
        result = SlackIngestionService(embedder=ChunkEmbedder()).ingest(
            token=token,
            channel_id=config["channel_id"],
        )
        return result, result.messages, slack_routes.to_response #TODO : refactor to return the response model

    raise ValueError(f"No ingestion pipeline for source type {source.source_type}")


def _apply_source_context(
    objects: Iterable[PermissionScope],
    source: ExternalDataSource,
    request: IngestDataRequest,
) -> None:

    for obj in objects:
        obj.team_id = request.team_id
        obj.department_id = request.department_id
        obj.access_scope = request.access_scope
        obj.external_data_source_id = source.id


def _source_record(
    source: ExternalDataSource, request: IngestDataRequest
) -> dict[str, Any]:
    """The connection this run was made against, as JSON.

    Built field by field rather than dumped, for one reason: `source.token` must
    not be in it. A secret does not reach a log, a response or a file, and a
    dump-everything helper is exactly how that rule gets broken later.
    """
    return {
        "external_data_source_id": str(source.id),
        "team_id": str(source.team_id),
        "department_id": str(request.department_id),
        "created_by_user_id": str(source.created_by_user_id),
        "name": source.name,
        "source_type": source.source_type.value,
        "status": source.status.value,
        "access_scope": request.access_scope.value,
        "config": source.config,
    }


def _failed(
    source: ExternalDataSource,
    request: IngestDataRequest,
    started_at: str,
    exc: IngestionError,
) -> dict[str, Any]:
    """The run file for an ingestion that did not finish."""
    return {
        "source": _source_record(source, request),
        "status": "FAILED",
        "started_at": started_at,
        "completed_at": _now(),
        # The client-safe message, the same text the synchronous endpoints
        # return. The upstream API's own error body stays in the server log.
        "error": {"type": type(exc).__name__, "message": exc.message},
        "result": None,
    }


def _write_run(source: ExternalDataSource, payload: dict[str, Any]) -> None:

    RUNS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIRECTORY / f"{source.source_type.value.lower()}_{source.id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Ingestion run written to %s", path.name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
