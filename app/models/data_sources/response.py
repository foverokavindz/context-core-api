from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.source_type import SourceType
from app.entities.data_sources.sync_run_status import SyncRunStatus
from app.entities.knowledge_sources.resource_type import ResourceType


class SyncRunResponse(BaseModel):
    """One ingestion run, as the client polls it.

    `PENDING -> RUNNING -> COMPLETED | FAILED` is the path the pipeline walks,
    and this is the shape a poll reads it through.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_data_source_id: UUID
    status: SyncRunStatus
    started_at: datetime | None
    completed_at: datetime | None
    resources_processed: int
    chunks_created: int
    chunks_updated: int
    chunks_deleted: int
    error_message: str | None  
    created_at: datetime


class DataSourceSummaryResponse(BaseModel):
    """A connected source as a row in a list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    source_type: SourceType
    status: SourceStatus
    config: dict | None  # where the connector points; never a secret
    last_synced_at: datetime | None
    created_at: datetime
    latest_run: SyncRunResponse | None
    resource_count: int
    chunk_count: int


class DataSourceDetailResponse(DataSourceSummaryResponse):
    """One connected source, with the fields a list does not need."""

    team_id: UUID
    created_by_user_id: UUID
    has_token: bool 


class IndexedResourceResponse(BaseModel):
    """One item this source contributed to the index."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    external_id: str | None
    resource_type: ResourceType
    version_key: str | None
    updated_at: datetime
    chunk_count: int


class DataSourceStatsResponse(BaseModel):
    """The team's sources counted up, for the dashboard tiles."""

    model_config = ConfigDict(from_attributes=True)

    total_sources: int
    connected_sources: int 
    healthy_syncs: int 
    pending_setup: int  
    indexed_items: int
    total_chunks: int
