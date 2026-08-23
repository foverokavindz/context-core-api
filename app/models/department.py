from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

DepartmentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
DepartmentDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class CreateDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DepartmentName
    description: DepartmentDescription | None = None


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
