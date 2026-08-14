"""Entity Models

Importing this package registers every mapper in the schema. Import it rather
than a single group: the groups now point at each other's mappers - `organization`
carries relationships into `teams`, and `teams` carries relationships into
`data_sources` - so importing `app.entities.organization` on its own leaves
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
from app.entities.organization import ApplicationRole, Department, JobTitle, User
from app.entities.teams import MemberRole, Team, TeamMember

__all__ = [
    "ApplicationRole",
    "CredentialType",
    "Department",
    "ExternalDataSource",
    "JobTitle",
    "MemberRole",
    "SourceCredentials",
    "SourceStatus",
    "SourceType",
    "SyncRun",
    "SyncRunStatus",
    "Team",
    "TeamMember",
    "User",
]
