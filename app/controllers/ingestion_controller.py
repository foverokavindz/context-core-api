import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.entities.data_sources.source_type import SourceType
from app.models.ingestion.request import REQUIRED_CONFIG_KEYS, IngestDataRequest
from app.models.ingestion.response import IngestStartedResponse
from app.services.ingestion_service import start_ingestion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


@router.post( 
    "/ingestData/{external_source}",
    status_code=202,
    response_model=IngestStartedResponse,
    summary="Connect an external source and start ingesting from it",
    response_description=(
        "The external data source that was created, and confirmation that its "
        "first run has been started. The run itself happens afterwards."
    ),
)
def ingest_data(
    external_source: str,
    request: IngestDataRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
) -> IngestStartedResponse:
    """Accept an ingestion request and start its pipeline in the background."""

    source_type = _resolve_source_type(external_source)
    _validate_config(source_type, request)
    return start_ingestion(request, source_type, background, session)


def _resolve_source_type(external_source: str) -> SourceType:
    """Turn the path segment into a SourceType. """
    try:
        return SourceType(external_source.upper())
    except ValueError:
        supported = ", ".join(member.value.lower() for member in SourceType)
        raise HTTPException(
            status_code=404,
            detail=f"Unknown external source '{external_source}'. Supported: {supported}.",
        )


def _validate_config(source_type: SourceType, request: IngestDataRequest) -> None:
    """Check the body carries what this source's connector needs. """
    if request.source_type is not source_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"source_type '{request.source_type.value}' does not match the "
                f"'{source_type.value.lower()}' in the URL path."
            ),
        )

    missing = [
        key
        for key in REQUIRED_CONFIG_KEYS[source_type]
        if not request.config.get(key, "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"config is missing {', '.join(missing)} for a "
                f"{source_type.value.lower()} source."
            ),
        )
