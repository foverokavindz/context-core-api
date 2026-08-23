import logging
from uuid import uuid4

from fastapi import HTTPException
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DepartmentNotFoundError,
    EmployeeAlreadyExistsError,
    TeamDepartmentMismatchError,
    TeamNotFoundError,
)
from app.entities import Team, TeamMember, User
from app.models.employee import CreateEmployeeRequest, EmployeeResponse
from app.repository.department_repository import DepartmentRepository
from app.repository.employee_repository import EmployeeRepository
from app.repository.team_repository import TeamRepository

logger = logging.getLogger(__name__)
password_hash = PasswordHash.recommended()


class EmployeeService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.departments = DepartmentRepository(session)
        self.employees = EmployeeRepository(session)
        self.teams = TeamRepository(session)

    def create_employee(self, request: CreateEmployeeRequest) -> EmployeeResponse:
        if self.departments.get_by_id(request.department_id) is None:
            raise DepartmentNotFoundError()

        team = self.teams.get_by_id(request.team_id)
        if team is None:
            raise TeamNotFoundError()

        if team.department_id != request.department_id:
            raise TeamDepartmentMismatchError()

        if self.employees.get_by_email(request.email) is not None:
            raise EmployeeAlreadyExistsError()

        user = User(
            id=uuid4(),
            email=request.email,
            username=None,
            password_hash=password_hash.hash(request.password.get_secret_value()),
            first_name=request.first_name,
            last_name=request.last_name,
            department_id=request.department_id,
            job_title_id=None,
            application_role=request.application_role,
            is_active=True,
        )
        membership = TeamMember(
            id=uuid4(),
            team_id=team.id,
            user_id=user.id,
            member_role=request.member_role,
        )

        try:
            self.employees.create(user, membership)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise EmployeeAlreadyExistsError()
        except SQLAlchemyError as exc:
            self.session.rollback()
            logger.error(
                "Could not create an employee (%s)", type(exc).__name__
            )
            raise HTTPException(500, "The employee could not be created.")

        return _to_response(user, membership, team)

    def list_employees(self) -> list[EmployeeResponse]:
        return [
            _to_response(user, membership, team)
            for user, membership, team in self.employees.list_all()
        ]


def _to_response(
    user: User, membership: TeamMember, team: Team
) -> EmployeeResponse:
    return EmployeeResponse(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        department_id=team.department_id,
        team_id=membership.team_id,
        application_role=user.application_role,
        member_role=membership.member_role,
    )
