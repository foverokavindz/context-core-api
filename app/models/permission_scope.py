from uuid import UUID

from pydantic import BaseModel

from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope


class PermissionScope(BaseModel):

    team_id: UUID | None = None
    department_id: UUID | None = None
    access_scope: ResourceAccessScope = ResourceAccessScope.TEAM
    external_data_source_id: UUID | None = None 