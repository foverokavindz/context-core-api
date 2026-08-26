from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

TeamName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
TeamDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    name: TeamName
    description: TeamDescription | None = None


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    name: str
    description: str | None
