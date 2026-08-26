from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

CompanyName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
Subtitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Logo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
]


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: CompanyName
    subtitle: Subtitle | None = None
    description: Description | None = None
    logo: Logo | None = None


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    subtitle: str | None
    description: str | None
    logo: str | None
