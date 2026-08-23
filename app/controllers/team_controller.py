from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db.dependencies import get_db
from app.models.team import CreateTeamRequest, TeamResponse
from app.services.team_service import TeamService

router = APIRouter(prefix="/api/v1", tags=["teams"])


@router.post(
    "/teams",
    status_code=status.HTTP_201_CREATED,
    response_model=TeamResponse,
)
def create_team(
    request: CreateTeamRequest,
    session: Session = Depends(get_db),
) -> TeamResponse:
    return TeamService(session).create_team(request)


@router.get(
    "/teams",
    status_code=status.HTTP_200_OK,
    response_model=list[TeamResponse],
)
def list_teams(
    session: Session = Depends(get_db),
) -> list[TeamResponse]:
    return TeamService(session).list_teams()
