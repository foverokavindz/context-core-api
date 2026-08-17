from uuid import UUID

from pydantic import BaseModel

from app.entities.data_sources.source_type import SourceType

PIPELINE_STARTED = "PIPELINE_STARTED"


class IngestStartedResponse(BaseModel):

    external_data_source_id: UUID
    sync_run_id: UUID 
    source_type: SourceType
    title: str
    status: str = PIPELINE_STARTED
