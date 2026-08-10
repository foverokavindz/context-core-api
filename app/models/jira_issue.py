"""JiraIssue - one Epic or Story, normalised into our own shape.

This model is THE boundary of the Jira pipeline, the counterpart to
RepositoryFile on the GitHub side. Everything upstream of it may know about
Jira's REST API, its field names and its Atlassian Document Format;
everything downstream of it - the linking pass, the chunker - must not.

By the time an issue is a JiraIssue, `description` is plain readable text. No
ADF JSON survives this boundary.
"""

from pydantic import BaseModel, Field

# The two issue types this pipeline ingests. Jira sites are free to rename the
# display case of a type, so every comparison against these goes through
# casefold() rather than ==.
EPIC_ISSUE_TYPE = "Epic"
STORY_ISSUE_TYPE = "Story"

# What an issue's type becomes when Jira did not report one. Recorded rather
# than guessed: an issue with no readable type is still worth chunking, it just
# cannot take part in Epic/Story linking.
UNKNOWN_ISSUE_TYPE = "Unknown"


class JiraIssue(BaseModel):
    """A single Jira issue plus the relationships we resolved for it.

    `child_issues` holds issue *keys*, never nested JiraIssue objects. Each
    child already has its own entry in the ingestion result and its own chunk,
    so embedding the objects here would duplicate every Story's description
    inside its Epic.
    """

    key: str
    project_key: str

    issue_type: str
    summary: str

    # Both optional: Jira permits an issue with no description, and a status
    # can be absent from a response that did not request or cannot see it.
    description: str | None = None
    status: str | None = None

    # The Epic this issue belongs to, as Jira reported it. Kept even when that
    # Epic was not part of this ingestion - a dangling pointer is more useful
    # than a silently dropped one.
    parent_key: str | None = None

    # Filled in by the ingestion service's linking pass, not by the parser.
    # default_factory rather than [] so two issues never share one list.
    child_issues: list[str] = Field(default_factory=list)

    @property
    def is_epic(self) -> bool:
        """True when Jira calls this an Epic, whatever case it used."""
        return self.issue_type.casefold() == EPIC_ISSUE_TYPE.casefold()

    @property
    def is_story(self) -> bool:
        """True when Jira calls this a Story, whatever case it used."""
        return self.issue_type.casefold() == STORY_ISSUE_TYPE.casefold()
