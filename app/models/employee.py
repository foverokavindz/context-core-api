from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.entities.organization.application_role import ApplicationRole
from app.entities.teams.member_role import MemberRole

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

EmployeeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
EmployeeEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=EMAIL_PATTERN),
]


class CreateEmployeeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: EmployeeName
    last_name: EmployeeName
    email: EmployeeEmail
    password: SecretStr = Field(min_length=8, max_length=128)
    department_id: UUID
    team_id: UUID
    application_role: ApplicationRole = ApplicationRole.EMPLOYEE
    member_role: MemberRole = MemberRole.TEAM_MEMBER


class EmployeeResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: str
    department_id: UUID
    team_id: UUID
    application_role: ApplicationRole
    member_role: MemberRole
