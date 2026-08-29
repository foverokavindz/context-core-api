from pydantic import Field

from app.entities.knowledge_sources.resource_type import ResourceType
from app.models.common.permission_scope import PermissionScope

EPIC_ISSUE_TYPE = "Epic"
STORY_ISSUE_TYPE = "Story"

UNKNOWN_ISSUE_TYPE = "Unknown"


class JiraIssue(PermissionScope):

    key: str
    project_key: str

    issue_type: str
    summary: str

    description: str | None = None
    status: str | None = None

    parent_key: str | None = None

    child_issues: list[str] = Field(default_factory=list)

    external_id: str
    title: str | None = None 
    version_key: str | None = None 
    resource_type: ResourceType = ResourceType.JIRA_ISSUE

    @property
    def is_epic(self) -> bool:
        """True when Jira calls this an Epic, whatever case it used."""
        return self.issue_type.casefold() == EPIC_ISSUE_TYPE.casefold()

    @property
    def is_story(self) -> bool:
        """True when Jira calls this a Story, whatever case it used."""
        return self.issue_type.casefold() == STORY_ISSUE_TYPE.casefold()
