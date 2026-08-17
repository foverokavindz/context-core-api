"""Connects an external source and starts ingesting from it.

Three steps, in this order:

    1. record the connection      ExternalDataSource
    2. queue the run              SyncRun, PENDING
    3. start the run              background pipeline
"""

import logging

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.background.pipeline.ingestion_pipeline import run_ingestion_pipeline
from app.entities.data_sources.source_type import SourceType
from app.models.ingestion.request import IngestDataRequest
from app.models.ingestion.response import IngestStartedResponse
from app.repository.external_data_source_repository import ExternalDataSourceRepository
from app.repository.sync_run_repository import SyncRunRepository

logger = logging.getLogger(__name__)


def start_ingestion(
    request: IngestDataRequest,
    source_type: SourceType,
    background: BackgroundTasks,
    session: Session,
) -> IngestStartedResponse:

    try:
        source = ExternalDataSourceRepository(session).create(request, source_type)
        sync_run = SyncRunRepository(session).create(source.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        logger.warning("Ingestion rejected: unknown team, department or user")
        raise HTTPException(
            status_code=400,
            detail=(
                "team_id, department_id or created_by_user_id does not match an "
                "existing record."
            ),
        )

    background.add_task(run_ingestion_pipeline, source, sync_run.id, request)

    logger.info(
        "Ingestion accepted for %s source %s (%s), sync run %s",
        source_type.value,
        source.id,
        source.name,
        sync_run.id,
    )
    return IngestStartedResponse(
        external_data_source_id=source.id,
        sync_run_id=sync_run.id,
        source_type=source_type,
        title=source.name,
    )
