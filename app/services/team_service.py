import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import DepartmentNotFoundError, TeamAlreadyExistsError
from app.entities import Team
from app.models.team import CreateTeamRequest, TeamResponse
from app.repository.department_repository import DepartmentRepository
from app.repository.team_repository import TeamRepository

logger = logging.getLogger(__name__)


class TeamService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.departments = DepartmentRepository(session)
        self.teams = TeamRepository(session)

    def create_team(self, request: CreateTeamRequest) -> TeamResponse:
        if self.departments.get_by_id(request.department_id) is None:
            raise DepartmentNotFoundError()

        if (
            self.teams.get_by_department_and_name(
                request.department_id, request.name
            )
            is not None
        ):
            raise TeamAlreadyExistsError()

        team = Team(
            id=uuid4(),
            department_id=request.department_id,
            name=request.name,
            description=request.description,
            created_by_user_id=None,
        )

        try:
            self.teams.create(team)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise TeamAlreadyExistsError()
        except SQLAlchemyError:
            self.session.rollback()
            logger.exception("Could not create a team")
            raise HTTPException(500, "The team could not be created.")

        return TeamResponse.model_validate(team)

    def list_teams(self) -> list[TeamResponse]:
        return [
            TeamResponse.model_validate(team) for team in self.teams.list_all()
        ]
