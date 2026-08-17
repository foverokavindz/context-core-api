import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import confluence_routes, github_routes, jira_routes, slack_routes
from app.core.db.session import SessionLocal
from app.core.exceptions import IngestionError
from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_type import SourceType
from app.entities.data_sources.sync_run_status import SyncRunStatus
from app.ingestion.confluence_ingestion_service import ConfluenceIngestionService
from app.ingestion.embedding_service import ChunkEmbedder
from app.ingestion.ingestion_service import GitHubIngestionService
from app.ingestion.jira_ingestion_service import JiraIngestionService
from app.ingestion.slack_ingestion_service import SlackIngestionService
from app.models.common.permission_scope import PermissionScope
from app.models.ingestion.request import IngestDataRequest
from app.repository.chunk_repository import ChunkRepository
from app.repository.external_data_source_repository import ExternalDataSourceRepository
from app.repository.resource_repository import ResourceRepository
from app.repository.sync_run_repository import SyncRunRepository

logger = logging.getLogger(__name__)

RUNS_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "runs"
RUN_FILE_FULL = True

PERSISTENCE_FAILED_MESSAGE = "The ingestion result could not be saved."

UNEXPECTED_FAILURE_MESSAGE = "The ingestion failed unexpectedly."


def run_ingestion_pipeline(
    source: ExternalDataSource,
    sync_run_id: UUID,
    request: IngestDataRequest,
    # this runs as a BackgroundTask, and it owns its
    # session end to end rather than borrowing the request's. 
    db_session: Callable[[], Session] = SessionLocal,
) -> None:
    """Ingest from one connected source, persist the result, and write the run file."""

    started_at = _now()
    logger.info(
        "Ingestion run %s starting for %s source %s (%s)",
        sync_run_id,
        source.source_type.value,
        source.id,
        source.name,
    )

    session = db_session()
    sync_runs = SyncRunRepository(session)

    try:
        
        sync_runs.update_status(
            sync_run_id, SyncRunStatus.RUNNING, started_at=_now_dt()
        )
        session.commit()

        result, items, to_response = _ingest(source, request)

        _apply_source_context(items, source, request)
        _apply_source_context(result.chunks, source, request)

        completed_at = _now_dt()

        # One transaction for all four writes. 
        ResourceRepository(session).add_new_resources(items)
        ChunkRepository(session).add_new_chunks(result.chunks)
        sync_runs.update_status(
            sync_run_id,
            SyncRunStatus.COMPLETED,
            completed_at=completed_at,
            resources_processed=len(items),
            chunks_created=len(result.chunks),
        )

        ExternalDataSourceRepository(session).mark_synced(source.id, completed_at)
        session.commit()

    except IngestionError as exc:
        logger.error(
            "Ingestion run %s failed for %s source %s: %s",
            sync_run_id,
            source.source_type.value,
            source.id,
            type(exc).__name__,
        )
        _record_failure(session, sync_runs, sync_run_id, exc.message)
        _write_run(
            source,
            _failed(
                source,
                request,
                sync_run_id,
                started_at,
                type(exc).__name__,
                exc.message,
            ),
        )
        return

    except SQLAlchemyError:
        # Nothing reached the database, and there is no client left to answer -
        # a failure that vanished here would be invisible, so it is logged in
        # full and recorded on the run row in the safe form.
        logger.exception(
            "Ingestion run %s could not be persisted for source %s",
            sync_run_id,
            source.id,
        )
        _record_failure(session, sync_runs, sync_run_id, PERSISTENCE_FAILED_MESSAGE)
        _write_run(
            source,
            _failed(
                source,
                request,
                sync_run_id,
                started_at,
                "PersistenceError",
                PERSISTENCE_FAILED_MESSAGE,
            ),
        )
        return

    except Exception:
        # The catch-all exists for one reason: the run row is already RUNNING,
        # and an exception past this point would leave it that way for ever.
        # A run that ended has to say so, whatever ended it.
        logger.exception(
            "Ingestion run %s failed unexpectedly for source %s",
            sync_run_id,
            source.id,
        )
        _record_failure(session, sync_runs, sync_run_id, UNEXPECTED_FAILURE_MESSAGE)
        _write_run(
            source,
            _failed(
                source,
                request,
                sync_run_id,
                started_at,
                "UnexpectedError",
                UNEXPECTED_FAILURE_MESSAGE,
            ),
        )
        return

    finally:
        session.close()

    response = to_response(result, full=RUN_FILE_FULL)

    logger.info(
        "Ingestion run %s finished for %s source %s: %d resource files, %d chunks",
        sync_run_id,
        source.source_type.value,
        source.id,
        len(items),
        len(result.chunks),
    )


    #TODO: remove this later, this is just for debugging and testing the run file
    _write_run(
        source,
        {
            "source": _source_record(source, request),
            "sync_run_id": str(sync_run_id),
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


def _record_failure(
    session: Session,
    sync_runs: SyncRunRepository,
    sync_run_id: UUID,
    message: str,
) -> None:
    """Mark the run FAILED on a session that may be holding a half-written run.

    The rollback comes first: resources may already have been flushed when the
    chunk insert failed, and none of them should survive a failed run.
    """
    try:
        session.rollback()
        sync_runs.update_status(
            sync_run_id,
            SyncRunStatus.FAILED,
            completed_at=_now_dt(),
            error_message=message,
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Could not record the failure on sync run %s", sync_run_id)


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
    sync_run_id: UUID,
    started_at: str,
    error_type: str,
    message: str,
) -> dict[str, Any]:
    """The run file for an ingestion that did not finish."""
    return {
        "source": _source_record(source, request),
        "sync_run_id": str(sync_run_id),
        "status": "FAILED",
        "started_at": started_at,
        "completed_at": _now(),
        # The client-safe message, the same text the synchronous endpoints
        # return. The upstream API's own error body stays in the server log.
        "error": {"type": error_type, "message": message},
        "result": None,
    }


def _write_run(source: ExternalDataSource, payload: dict[str, Any]) -> None:

    RUNS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIRECTORY / f"{source.source_type.value.lower()}_{source.id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Ingestion run written to %s", path.name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    """The same instant as _now(), for the TIMESTAMPTZ columns."""
    return datetime.now(timezone.utc)
