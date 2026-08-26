from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.common.api_response import ApiResponse
from app.models.team import CreateTeamRequest, TeamResponse
from app.services.team_service import TeamService

router = APIRouter(prefix="/api/v1", tags=["teams"])


@router.post(
    "/teams",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[TeamResponse],
)
def create_team(
    request: CreateTeamRequest,
    session: Session = Depends(get_db),
) -> ApiResponse[TeamResponse]:
    team = TeamService(session).create_team(request)
    return ApiResponse[TeamResponse].ok(team)


@router.get(
    "/teams",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse[list[TeamResponse]],
)
def list_teams(
    session: Session = Depends(get_db),
) -> ApiResponse[list[TeamResponse]]:
    teams = TeamService(session).list_teams()
    return ApiResponse[list[TeamResponse]].ok(teams)
