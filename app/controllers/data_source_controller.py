from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.common.api_response import ApiResponse
from app.models.data_sources.response import (
    DataSourceDetailResponse,
    DataSourceStatsResponse,
    DataSourceSummaryResponse,
    IndexedResourceResponse,
    SyncRunResponse,
)
from app.services.data_source_service import DataSourceService

router = APIRouter(prefix="/api/v1", tags=["data-sources"])


@router.get(
    "/syncRuns/{sync_run_id}",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[SyncRunResponse],
    summary="Read one ingestion run",
    response_description=(
        "The run as it currently stands. Polled while an ingestion is in "
        "progress, until the status settles on COMPLETED or FAILED."
    ),
)
def get_sync_run(
    sync_run_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[SyncRunResponse]:
    run = DataSourceService(session).get_sync_run(sync_run_id)
    return ApiResponse[SyncRunResponse].ok(run)


@router.get(
    "/dataSources",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[DataSourceSummaryResponse]],
    summary="List a team's connected sources",
)
def list_data_sources(
    team_id: UUID = Query(description="Whose sources to list."),
    session: Session = Depends(get_db),
) -> ApiResponse[list[DataSourceSummaryResponse]]:
    sources = DataSourceService(session).list_by_team(team_id)
    return ApiResponse[list[DataSourceSummaryResponse]].ok(sources)


@router.get(
    "/dataSources/stats",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[DataSourceStatsResponse],
    summary="Count up a team's sources",
)
def get_data_source_stats(
    team_id: UUID = Query(description="Whose sources to count."),
    session: Session = Depends(get_db),
) -> ApiResponse[DataSourceStatsResponse]:
    stats = DataSourceService(session).get_team_stats(team_id)
    return ApiResponse[DataSourceStatsResponse].ok(stats)


@router.get(
    "/dataSources/{data_source_id}",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[DataSourceDetailResponse],
    summary="Read one connected source",
    response_description=(
        "The connection and what it has produced. Never its token - `has_token` "
        "says whether one is stored, and that is all a response carries."
    ),
)
def get_data_source(
    data_source_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[DataSourceDetailResponse]:
    source = DataSourceService(session).get_by_id(data_source_id)
    return ApiResponse[DataSourceDetailResponse].ok(source)


@router.get(
    "/dataSources/{data_source_id}/syncRuns",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[SyncRunResponse]],
    summary="List one source's ingestion runs",
)
def list_source_sync_runs(
    data_source_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[list[SyncRunResponse]]:
    runs = DataSourceService(session).list_sync_runs(data_source_id)
    return ApiResponse[list[SyncRunResponse]].ok(runs)


@router.get(
    "/dataSources/{data_source_id}/resources",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[IndexedResourceResponse]],
    summary="List what one source has indexed",
)
def list_source_resources(
    data_source_id: UUID,
    session: Session = Depends(get_db),
) -> ApiResponse[list[IndexedResourceResponse]]:
    resources = DataSourceService(session).list_resources(data_source_id)
    return ApiResponse[list[IndexedResourceResponse]].ok(resources)
