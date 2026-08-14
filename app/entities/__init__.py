"""Entity Models

Importing this package registers every mapper in the schema. Import it rather
than a single group: the groups now point at each other's mappers - `organization`
carries relationships into `teams`, `teams` carries relationships into
`data_sources` and `knowledge_sources`, and `knowledge_sources` points back at all
three - so importing `app.entities.organization` on its own leaves
`Department.teams` with nothing to resolve to.
"""

from app.entities.data_sources import (
    CredentialType,
    ExternalDataSource,
    SourceCredentials,
    SourceStatus,
    SourceType,
    SyncRun,
    SyncRunStatus,
)
from app.entities.knowledge_sources import (
    Chunk,
    ChunkType,
    Resource,
    ResourceAccessScope,
    ResourceType,
)
from app.entities.organization import ApplicationRole, Department, JobTitle, User
from app.entities.teams import MemberRole, Team, TeamMember

__all__ = [
    "ApplicationRole",
    "Chunk",
    "ChunkType",
    "CredentialType",
    "Department",
    "ExternalDataSource",
    "JobTitle",
    "MemberRole",
    "Resource",
    "ResourceAccessScope",
    "ResourceType",
    "SourceCredentials",
    "SourceStatus",
    "SourceType",
    "SyncRun",
    "SyncRunStatus",
    "Team",
    "TeamMember",
    "User",
]
