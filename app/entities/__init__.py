"""Entity Models

Importing this package registers every mapper in the schema. Import it rather
than a single group: `organization` now carries relationships that point at
`teams`, so importing `app.entities.organization` on its own leaves
`Department.teams` with nothing to resolve to.
"""

from app.entities.organization import ApplicationRole, Department, JobTitle, User
from app.entities.teams import MemberRole, Team, TeamMember

__all__ = [
    "ApplicationRole",
    "Department",
    "JobTitle",
    "MemberRole",
    "Team",
    "TeamMember",
    "User",
]
