from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.entities.data_sources.source_type import SourceType
from app.entities.knowledge_sources.resource_access_scope import ResourceAccessScope

REQUIRED_CONFIG_KEYS: dict[SourceType, tuple[str, ...]] = {
    SourceType.GITHUB: ("repository",),
    SourceType.JIRA: ("site_url", "email", "project_key"),
    SourceType.CONFLUENCE: ("site_url", "email", "space_key"),
    SourceType.SLACK: ("channel_id",),
}


class IngestDataRequest(BaseModel):

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=255,
        description="What to call this connection. Becomes the display name on "
        "the external data source - the repository, project, space or channel "
        "as a human would refer to it.",
        examples=["TrackIt API"],
    )

    team_id: UUID
    department_id: UUID
    access_scope: ResourceAccessScope = ResourceAccessScope.TEAM

    created_by_user_id: UUID = Field(
        description="Who connected the source. Recorded as authorship - it is "
        "not who may read what the source produces.",
    )

    source_type: SourceType = Field(
        description="Must agree with the source in the URL path. Carried in the "
        "body as well so the stored row does not depend on how it was routed.",
    )

    config: dict[str, str] = Field(
        description="Where this connector points. GitHub: repository, and "
        "optionally branch. Jira: site_url, email, project_key. Confluence: "
        "site_url, email, space_key. Slack: channel_id. Never a secret - the "
        "token has its own field.",
        examples=[{"repository": "my-org/backend", "branch": "main"}],
    )

    token: SecretStr = Field(
        description="The access token this source authenticates with. Stored on "
        "the external data source so a later sync can re-run without asking for "
        "it again; never logged, never returned, never written to the run file.",
    )
