import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.entities.data_sources.external_data_source import ExternalDataSource
from app.entities.data_sources.source_status import SourceStatus
from app.entities.data_sources.sync_run import SyncRun
from app.entities.data_sources.sync_run_status import SyncRunStatus
from app.entities.knowledge_sources.resource import Resource
from app.models.data_sources.response import (
    DataSourceDetailResponse,
    DataSourceStatsResponse,
    DataSourceSummaryResponse,
    IndexedResourceResponse,
    SyncRunResponse,
)
from app.repository.chunk_repository import ChunkRepository
from app.repository.external_data_source_repository import ExternalDataSourceRepository
from app.repository.resource_repository import ResourceRepository
from app.repository.sync_run_repository import SyncRunRepository

logger = logging.getLogger(__name__)

RUN_HISTORY_LIMIT = 20
INDEXED_RESOURCE_LIMIT = 100

SOURCE_NOT_FOUND = "No connected source with that id."
SYNC_RUN_NOT_FOUND = "No sync run with that id."


class DataSourceService:
    """Read-only queries over `external_data_sources` and `sync_runs`."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.sources = ExternalDataSourceRepository(session)
        self.sync_runs = SyncRunRepository(session)
        self.resources = ResourceRepository(session)
        self.chunks = ChunkRepository(session)

    def list_by_team(self, team_id: UUID) -> list[DataSourceSummaryResponse]:
        """Every source this team has connected, each with its last run."""
        sources = self.sources.list_by_team(team_id)
        if not sources:
            return []

        source_ids = [source.id for source in sources]
        latest_runs = self.sync_runs.latest_by_source_ids(source_ids)
        resource_counts = self.resources.count_by_source_ids(source_ids)
        chunk_counts = self.chunks.count_by_source_ids(source_ids)

        return [
            _to_summary(
                source,
                latest_runs.get(source.id),
                resource_counts.get(source.id, 0),
                chunk_counts.get(source.id, 0),
            )
            for source in sources
        ]

    def get_by_id(self, source_id: UUID) -> DataSourceDetailResponse:
        source = self._require_source(source_id)
        latest_runs = self.sync_runs.latest_by_source_ids([source.id])
        resource_counts = self.resources.count_by_source_ids([source.id])
        chunk_counts = self.chunks.count_by_source_ids([source.id])

        summary = _to_summary(
            source,
            latest_runs.get(source.id),
            resource_counts.get(source.id, 0),
            chunk_counts.get(source.id, 0),
        )
        return DataSourceDetailResponse(
            **summary.model_dump(),
            team_id=source.team_id,
            created_by_user_id=source.created_by_user_id,
            has_token=bool(source.token),  # whether one is stored, never which one
        )

    def get_sync_run(self, sync_run_id: UUID) -> SyncRunResponse:
        """One run. This is the endpoint the client polls while ingesting."""
        run = self.sync_runs.get_by_id(sync_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=SYNC_RUN_NOT_FOUND)
        return SyncRunResponse.model_validate(run)

    def list_sync_runs(self, source_id: UUID) -> list[SyncRunResponse]:
        self._require_source(source_id)
        runs = self.sync_runs.list_by_source(source_id, RUN_HISTORY_LIMIT)
        return [SyncRunResponse.model_validate(run) for run in runs]

    def list_resources(self, source_id: UUID) -> list[IndexedResourceResponse]:
        """What this source contributed to the index, with each item's chunk count."""
        self._require_source(source_id)
        resources = self.resources.list_by_source(source_id, INDEXED_RESOURCE_LIMIT)
        if not resources:
            return []

        external_ids = [
            resource.external_id
            for resource in resources
            if resource.external_id is not None
        ]
        chunk_counts = self.chunks.count_by_external_ids(source_id, external_ids)

        return [_to_indexed_resource(resource, chunk_counts) for resource in resources]

    def get_team_stats(self, team_id: UUID) -> DataSourceStatsResponse:
        """The team's sources counted up, for the dashboard tiles."""
        sources = self.sources.list_by_team(team_id)
        source_ids = [source.id for source in sources]
        latest_runs = self.sync_runs.latest_by_source_ids(source_ids)
        resource_counts = self.resources.count_by_source_ids(source_ids)
        chunk_counts = self.chunks.count_by_source_ids(source_ids)

        connected = 0
        healthy = 0
        pending = 0

        for source in sources:
            latest = latest_runs.get(source.id)

            if latest is not None and latest.status is SyncRunStatus.COMPLETED:
                healthy += 1

            if source.status is SourceStatus.ACTIVE and source.last_synced_at is not None:
                connected += 1

            if latest is None or latest.status is SyncRunStatus.FAILED:
                pending += 1

        return DataSourceStatsResponse(
            total_sources=len(sources),
            connected_sources=connected,
            healthy_syncs=healthy,
            pending_setup=pending,
            indexed_items=sum(resource_counts.values()),
            total_chunks=sum(chunk_counts.values()),
        )

    def _require_source(self, source_id: UUID) -> ExternalDataSource:
        source = self.sources.get_by_id(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail=SOURCE_NOT_FOUND)
        return source


def _to_summary(
    source: ExternalDataSource,
    latest_run: SyncRun | None,
    resource_count: int,
    chunk_count: int,
) -> DataSourceSummaryResponse:

    return DataSourceSummaryResponse(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        status=source.status,
        config=source.config,
        last_synced_at=source.last_synced_at,
        created_at=source.created_at,
        latest_run=(
            SyncRunResponse.model_validate(latest_run)
            if latest_run is not None
            else None
        ),
        resource_count=resource_count,
        chunk_count=chunk_count,
    )


def _to_indexed_resource(
    resource: Resource, chunk_counts: dict[str, int]
) -> IndexedResourceResponse:
    return IndexedResourceResponse(
        id=resource.id,
        title=resource.title,
        external_id=resource.external_id,
        resource_type=resource.resource_type,
        version_key=resource.version_key,
        updated_at=resource.updated_at,
        chunk_count=chunk_counts.get(resource.external_id or "", 0),
    )
