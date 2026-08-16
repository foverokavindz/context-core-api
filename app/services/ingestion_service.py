"""Connects an external source and starts ingesting from it.

Two steps, in this order:

    1. record the connection      ExternalDataSource
    2. start the run              background pipeline

and the response goes back between them, so a caller is not held open for the
minutes a real repository takes.

The order is the one docs/todo.md describes for `DataSourceService.connect_source()`
minus the credential: the source row is written first, and for now the token
sits on it rather than in a `source_credentials` row. That is a deliberate
simplification for this phase and is recorded as such - `credential_id` stays
null, and nothing here pretends otherwise.

Not to be confused with `app/ingestion/ingestion_service.py`, which is GitHub's
own pipeline. This one owns *which* pipeline runs; that one owns how GitHub's
runs.
"""

import logging
from uuid import uuid4

from fastapi import BackgroundTasks

from app.background.pipeline.ingestion_pipeline import run_ingestion_pipeline
from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType
from app.models.ingestion.request import IngestDataRequest
from app.models.ingestion.response import IngestStartedResponse

logger = logging.getLogger(__name__)


def start_ingestion(
    request: IngestDataRequest,
    source_type: SourceType,
    background: BackgroundTasks,
) -> IngestStartedResponse:

    source = _create_external_data_source(request, source_type)

    background.add_task(run_ingestion_pipeline, source, request) #background task to run the ingestion pipeline

    logger.info(
        "Ingestion accepted for %s source %s (%s)",
        source_type.value,
        source.id,
        source.name,
    )
    return IngestStartedResponse(
        external_data_source_id=source.id,
        source_type=source_type,
        title=source.name,
    )


#TODO this will remove later
def _create_external_data_source(
    request: IngestDataRequest, source_type: SourceType
) -> ExternalDataSource:
    
    source = ExternalDataSource(
        id=uuid4(),
        team_id=request.team_id,
        created_by_user_id=request.created_by_user_id,
        name=request.title,
        source_type=source_type,
        status=SourceStatus.ACTIVE,
        config=dict(request.config),
        token=request.token.get_secret_value(),
    )
    # TODO: session.add(source); session.commit() - needs an engine and a
    # driver. The credential row this token really belongs in, and encrypting
    # it, are both in docs/todo.md.
    return source
