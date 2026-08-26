from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr

from app.entities.organization.application_role import ApplicationRole
from app.models.employee import EmployeeEmail


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmployeeEmail
    password: SecretStr


class AuthenticatedUserContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    application_role: ApplicationRole
    team_id: UUID
    department_id: UUID


class CurrentUserResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: str
    application_role: ApplicationRole
    team_id: UUID
    department_id: UUID


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: CurrentUserResponse
