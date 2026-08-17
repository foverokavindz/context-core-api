"""Entity Models

Importing this package registers every mapper in the schema. Import it rather
than a single group: the groups now point at each other's mappers - `organization`
carries relationships into `teams`, `documents` and `chat`, `teams` carries
relationships into `data_sources`, `knowledge_sources` and `chunks`, and `chat`
points back at `chunks` and `knowledge_sources` - so importing
`app.entities.organization` on its own leaves `Department.teams` with nothing to
resolve to.
"""

from app.entities.chat import ChatSession, ChatSessionMessage, Citation, MessageRole
from app.entities.chunks import Chunk
from app.entities.data_sources import (
    CredentialType,
    ExternalDataSource,
    SourceCredentials,
    SourceStatus,
    SourceType,
    SyncRun,
    SyncRunStatus,
)
from app.entities.documents import Document, DocumentStatus
from app.entities.knowledge_sources import Resource, ResourceAccessScope, ResourceType
from app.entities.organization import ApplicationRole, Department, JobTitle, User
from app.entities.teams import MemberRole, Team, TeamMember

__all__ = [
    "ApplicationRole",
    "ChatSession",
    "ChatSessionMessage",
    "Chunk",
    "Citation",
    "CredentialType",
    "Department",
    "Document",
    "DocumentStatus",
    "ExternalDataSource",
    "JobTitle",
    "MemberRole",
    "MessageRole",
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
